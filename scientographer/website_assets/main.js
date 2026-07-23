// Interactive semantic map viewer.
// Same architecture as the citation-graph viewer, but node x/y come from a 2D
// UMAP of text embeddings. There are two selectable DATASETS (see DATASETS
// below), each built from a different embedding model:
//   - specter (data/)        SPECTER2 embeddings: two groupings, BERTopic
//                            topics + Leiden communities.
//   - gemini  (gemini_data/) Gemini embeddings: a semantic-cluster grouping
//                            plus the (embedding-independent) Leiden citation
//                            communities, shared with the specter dataset.
// A dataset declares one or more GROUPINGS; each grouping has its own legend +
// mute set, and all mutes stack. The "Color by" options and legend blocks are
// generated from the active dataset's groupings. A global toggle overlays the
// citation edges on the semantic layout.

import Graph from "https://cdn.jsdelivr.net/npm/graphology@0.25.4/+esm";
import { Sigma } from "https://cdn.jsdelivr.net/npm/sigma@2.4.0/+esm";

// ── Dataset definitions ─────────────────────────────────────────────────────
// Each grouping: key (also the "Color by" value), label (detail panel + color
// menu), legendLabel (legend heading), file (json under the dataset dir),
// nodeField (per-node id into that json), colorField (per-node color), and
// noneLabel (the "ungrouped" bucket). `semantic: true` marks the text-embedding
// (topic / semantic-cluster) grouping — the one the "Intra-topic citation
// islands" view operates on. Exactly one grouping per dataset carries it.
// Single dataset built by motor_learning_network/build_website.py from one
// GraphML: positions come from the graphml's layout (x/y), the semantic "topic"
// grouping from the graphml `topic` attribute, and the citation "community"
// grouping from the graphml `cpm_communities_at_res=*` attribute. The bundle
// (nodes.json / clusters.json / communities.json / abstracts.json / edges_*.bin)
// lives in the `network_data/` sibling directory.
const DATASETS = {
  network: {
    label: "Citation network",
    dir: "network_data",
    subtitle: "Citation-network layout",
    groupings: [
      { key: "community", label: "Community", legendLabel: "Communities", file: "communities.json", nodeField: "community", colorField: "community_color", noneLabel: "No community", citation: true },
      { key: "cluster", label: "Topic", legendLabel: "Topics", file: "clusters.json", nodeField: "cluster", colorField: "color", noneLabel: "No topic", semantic: true },
    ],
  },
};
const DEFAULT_DATASET = "network";

// ── State ─────────────────────────────────────────────────────────────────
const state = {
  dataset: DEFAULT_DATASET,
  nodesData: null, // { year_min, year_max, nodes: [...] }
  groupData: {}, // grouping key -> { "0": {...}, ... }
  outCSR: null,
  inCSR: null,
  abstracts: null,
  abstractsPromise: null,

  graph: null,
  renderer: null,

  selectedNode: null,
  neighborSet: new Set(),
  hoveredNode: null,

  yearMin: 0,
  yearMax: 9999,

  muted: {}, // grouping key -> Set of muted ids (+ NONE_ID)
  legendEls: {}, // grouping key -> <ul> element
  colorBy: "topic", // a grouping key | year | indegree
  highlightBridges: false,
  showEdges: false,
  _citeLayerBuilt: false,
  shiftDown: false,

  // Intra-topic citation islands: for the dataset's semantic grouping, the
  // connected components of the citation subgraph induced on each topic's
  // papers. Reveals papers that share a topic but form disconnected citation
  // bodies. Computed at runtime; reset when the dataset changes.
  showIntraTopic: false,
  _intraLayerBuilt: false,
  topicComponents: null, // { compRank, compSizeOf } — see computeTopicComponents

  // Community integration ("Color by → Integration"): per Leiden community, how
  // much it cites outside itself vs chance. Computed at runtime over the CSR
  // edges; reset when the dataset changes. See computeCommunityIntegration.
  communityIntegration: null, // { byId: {cid: {...}}, lo, mid, hi, Q, internalShare }

  // "Until-year" time snapshots: the active dataset's snapshots.json (per-cutoff
  // re-layouts using only papers up to that year; colour/grouping is unchanged).
  // state.snapshot === null means the full "now" layout (base node x/y). Reset on
  // dataset switch. See setupSnapshots / applySnapshot.
  snapshotData: null, // { cutoffs:[...], snapshots:{ "2010": {coords, centroids} } } | null
  snapshot: null, // null (All) | { cutoff, visible:Set(indexStr), centroids:{cluster,community} }

  // floating-label bookkeeping for the currently-active grouping
  labelMode: "dynamic", // dynamic | always
  activeLabelEls: {},
  activeLabelData: null,
  activeLabelOrder: [], // gids sorted by size desc (for progressive reveal)

  // v1-like filtering additions:
  filteredSet: null,
  filters: { title: "", author: "", abstract: "", journal: "", keywords: "", mesh: "" },
  index: null,

  table: null,
  _refilterTimer: null,
};

// Sentinel id for the "ungrouped" bucket (no topic / no community): papers
// whose group isn't a named entry in topics.json / communities.json.
const NONE_ID = "__none__";

// Groupings declared by the active dataset.
function activeGroupings() {
  return DATASETS[state.dataset].groupings;
}
function groupingConfig(key) {
  return activeGroupings().find((g) => g.key === key) || null;
}
// Descriptor for a grouping (config + live data/mute set) so legend/label/
// detail code is written once. Returns null for non-grouping color modes.
function grouping(key) {
  const cfg = groupingConfig(key);
  if (!cfg) return null;
  return { ...cfg, data: state.groupData[key], muted: state.muted[key] };
}
// The active dataset's text-embedding (topic / semantic-cluster) grouping —
// the basis for the intra-topic citation islands view. Null if none is marked.
function semanticGrouping() {
  const cfg = activeGroupings().find((g) => g.semantic);
  return cfg ? grouping(cfg.key) : null;
}
// The active dataset's Leiden citation-community grouping — the basis for the
// community-integration color mode. Null if the dataset has none.
function citationGrouping() {
  const cfg = activeGroupings().find((g) => g.citation);
  return cfg ? grouping(cfg.key) : null;
}

// ── Boot ──────────────────────────────────────────────────────────────────
main().catch((err) => {
  console.error(err);
  const el = document.getElementById("loading");
  if (el) el.textContent = "Failed to load: " + err.message;
});

async function main() {
  // One-time wiring (event listeners on static DOM); the renderer + per-frame
  // hooks are created lazily on the first loadDataset call.
  initShiftTracking();
  initControls();
  initTabs();
  initGlobalFilters();
  initYearControls();
  initDatasetSelector();
  initSnapshotControl();

  await loadDataset(DEFAULT_DATASET);

  document.getElementById("loading")?.classList.add("hidden");
}

// Load (or switch to) a dataset: fetch its files, (re)build the graph, and
// regenerate the dataset-dependent UI (color-by menu, legends, labels, years).
async function loadDataset(name) {
  const cfg = DATASETS[name];
  state.dataset = name;

  const groupingFetches = cfg.groupings.map((g) =>
    fetch(`${cfg.dir}/${g.file}`).then((r) => r.json())
  );
  const [nodesPayload, outBuf, inBuf, ...groupingPayloads] = await Promise.all([
    fetch(`${cfg.dir}/nodes.json`).then((r) => r.json()),
    fetch(`${cfg.dir}/edges_out.bin`).then((r) => r.arrayBuffer()),
    fetch(`${cfg.dir}/edges_in.bin`).then((r) => r.arrayBuffer()),
    ...groupingFetches,
  ]);

  state.nodesData = nodesPayload;
  state.outCSR = parseCSR(outBuf);
  state.inCSR = parseCSR(inBuf);
  state.yearMin = nodesPayload.year_min;
  state.yearMax = nodesPayload.year_max;

  // Per-grouping data + a fresh (empty) mute set.
  state.groupData = {};
  state.muted = {};
  cfg.groupings.forEach((g, i) => {
    state.groupData[g.key] = groupingPayloads[i];
    state.muted[g.key] = new Set();
  });
  state.colorBy = cfg.groupings[0].key;

  // Reset transient view state and the abstracts cache (per-dataset).
  state.selectedNode = null;
  state.neighborSet = new Set();
  state.filteredSet = null;
  state.abstracts = null;
  state.abstractsPromise = null;
  state._citeLayerBuilt = false;
  // The components belong to the previous dataset's semantic grouping; the graph
  // clear in buildGraph() already dropped the intra edge layer.
  state.topicComponents = null;
  state._intraLayerBuilt = false;
  state.communityIntegration = null; // belongs to the previous dataset's communities
  state.snapshotData = null; // belongs to the previous dataset
  state.snapshot = null; // back to the full "now" layout

  document.getElementById("subtitle").innerHTML = cfg.subtitle;

  buildIndex();
  buildGraph(); // clears + repopulates the graph (and any edge layers)
  if (!state.renderer) {
    initSigma();
    initGroupLabels();
    initHover();
    initSelection();
  }

  buildColorByOptions();
  buildLegends();
  renderGroupLabels();
  resetYearControls();
  await setupSnapshots(cfg);

  // The islands toggle persists across dataset switches; recompute + rebuild it
  // against the new dataset's semantic grouping if it's on.
  if (state.showIntraTopic) {
    computeTopicComponents();
    buildIntraTopicLayer();
  }

  hideDetail();
  updateSelectedCount();
  state.renderer.refresh();
}

function initDatasetSelector() {
  const sel = document.getElementById("dataset-select");
  if (!sel) return;
  sel.value = state.dataset;
  sel.addEventListener("change", async (e) => {
    sel.disabled = true;
    document.getElementById("loading")?.classList.remove("hidden");
    try {
      await loadDataset(e.target.value);
    } finally {
      sel.disabled = false;
      document.getElementById("loading")?.classList.add("hidden");
    }
  });
}

// ── Time snapshots ("until-year" layouts) ───────────────────────────────────
// One-time: wire the snapshot dropdown (options are (re)built per dataset).
function initSnapshotControl() {
  const sel = document.getElementById("snapshot-select");
  if (!sel) return;
  sel.addEventListener("change", (e) => applySnapshot(e.target.value));
}

// Per-dataset: fetch snapshots.json (if the dataset declares snapshots) and
// (re)populate the dropdown. Defaults to "All" (the full present-day layout).
async function setupSnapshots(cfg) {
  const row = document.getElementById("snapshot-row");
  const sel = document.getElementById("snapshot-select");
  state.snapshotData = null;
  state.snapshot = null;
  if (!row || !sel || !cfg.snapshots) {
    if (row) row.hidden = true;
    return;
  }
  try {
    state.snapshotData = await fetch(`${cfg.dir}/snapshots.json`).then((r) => (r.ok ? r.json() : null));
  } catch {
    state.snapshotData = null;
  }
  if (!state.snapshotData || !Array.isArray(state.snapshotData.cutoffs)) {
    row.hidden = true;
    return;
  }
  const cutoffs = state.snapshotData.cutoffs.slice().sort((a, b) => b - a); // newest first
  sel.innerHTML =
    `<option value="all">All (now)</option>` +
    cutoffs.map((c) => `<option value="${c}">&le; ${c}</option>`).join("");
  sel.value = "all";
  row.hidden = false;
}

// Switch to a snapshot ("all" = full layout, or a cutoff-year string). Swaps node
// positions, hides papers absent from the snapshot, and reframes the camera. The
// floating labels reposition via per-snapshot centroids (see positionLabels).
function applySnapshot(value) {
  const snap = value !== "all" && state.snapshotData
    ? state.snapshotData.snapshots[value]
    : null;

  // Always reset to the base node sizes first; a forceatlas snapshot then
  // overrides them with its Gephi sizes below.
  restoreBaseSizes();

  if (!snap) {
    state.snapshot = null;
    restoreBasePositions();
  } else {
    const nodes = state.nodesData.nodes;
    const coords = snap.coords;
    const visible = new Set();
    let sx = 0, sy = 0;
    for (let i = 0; i < nodes.length; i++) {
      const c = coords[nodes[i].id];
      if (!c) continue; // published after the cutoff (or unembedded) → hidden
      state.graph.setNodeAttribute(String(i), "x", c[0]);
      state.graph.setNodeAttribute(String(i), "y", c[1]);
      visible.add(String(i));
      sx += c[0];
      sy += c[1];
    }
    // Park hidden papers on the visible centroid: they don't render, but sigma's
    // layout extent (and thus the camera reset) is computed over *all* nodes, so
    // leaving them at stale coordinates would keep the view zoomed out to nothing.
    const n = visible.size || 1;
    const cx = sx / n, cy = sy / n;
    for (let i = 0; i < nodes.length; i++) {
      if (visible.has(String(i))) continue;
      state.graph.setNodeAttribute(String(i), "x", cx);
      state.graph.setNodeAttribute(String(i), "y", cy);
    }
    // Per-grouping member counts within this snapshot, so labels can be limited
    // to groups that are actually substantial in the given year (see MIN_SNAPSHOT
    // _LABEL in positionLabels). Computed here once per snapshot switch.
    const counts = {};
    for (const g of activeGroupings()) counts[g.key] = {};
    for (const idxStr of visible) {
      const r = nodes[parseInt(idxStr, 10)];
      for (const g of activeGroupings()) {
        const gid = String(r[g.nodeField]);
        counts[g.key][gid] = (counts[g.key][gid] || 0) + 1;
      }
    }
    // Apply the snapshot's own per-node sizes (forceatlas / Gephi exports).
    if (snap.sizes) {
      for (const idxStr of visible) {
        const s = snap.sizes[nodes[parseInt(idxStr, 10)].id];
        if (s == null) continue;
        const rs = snapshotRenderSize(s);
        state.graph.setNodeAttribute(idxStr, "size", rs);
        state.graph.setNodeAttribute(idxStr, "_baseSize", rs);
      }
    }
    state.snapshot = { cutoff: value, visible, centroids: snap.centroids || {}, counts };
  }

  state.renderer.refresh(); // recomputes the layout extent for the new positions
  resetCamera();
  scheduleRefilter();
}

function restoreBasePositions() {
  const nodes = state.nodesData.nodes;
  for (let i = 0; i < nodes.length; i++) {
    state.graph.setNodeAttribute(String(i), "x", nodes[i].x);
    state.graph.setNodeAttribute(String(i), "y", nodes[i].y);
  }
}

// Reset every node's render size back to its base (the dataset's own size). Used
// before applying a snapshot's Gephi sizes and when returning to the "All" view.
function restoreBaseSizes() {
  const nodes = state.nodesData.nodes;
  for (let i = 0; i < nodes.length; i++) {
    const rs = nodeRenderSize(nodes[i]);
    state.graph.setNodeAttribute(String(i), "size", rs);
    state.graph.setNodeAttribute(String(i), "_baseSize", rs);
  }
}

function resetCamera() {
  const cam = state.renderer.getCamera();
  if (typeof cam.animatedReset === "function") cam.animatedReset({ duration: 500 });
  else cam.animate({ x: 0.5, y: 0.5, ratio: 1 }, { duration: 500 });
}

// Snapshot-aware centroid for a group (falls back to the base centroid). Returns
// null when a snapshot is active but this group has no papers in it.
function groupCentroid(key, gid) {
  const g = grouping(key);
  const c = g && g.data[gid];
  if (!c) return null;
  if (state.snapshot) {
    const ov = state.snapshot.centroids[key];
    return ov ? ov[gid] || null : c.centroid;
  }
  return c.centroid;
}

// ── CSR helpers ───────────────────────────────────────────────────────────
function parseCSR(buf) {
  const headerView = new DataView(buf, 0, 4);
  const n = headerView.getUint32(0, true);
  const offsets = new Uint32Array(buf, 4, n + 1);
  const totalEdges = offsets[n];
  const targets = new Uint32Array(buf, 4 + (n + 1) * 4, totalEdges);
  return { n, offsets, targets };
}
function csrNeighbors(csr, idx) {
  return csr.targets.subarray(csr.offsets[idx], csr.offsets[idx + 1]);
}

// ── Index build (fast filtering) ──────────────────────────────────────────
function buildIndex() {
  const nodes = state.nodesData.nodes;
  const title = new Array(nodes.length);
  const authors = new Array(nodes.length);
  const journal = new Array(nodes.length);
  const doi = new Array(nodes.length);
  const keywords = new Array(nodes.length);
  const mesh = new Array(nodes.length);

  for (let i = 0; i < nodes.length; i++) {
    const r = nodes[i];
    title[i] = (r.title || "").toLowerCase();
    authors[i] = (r.authors || "").toLowerCase();
    journal[i] = (r.journal || "").toLowerCase();
    doi[i] = (r.doi || "").toLowerCase();
    keywords[i] = (r.keywords || "").toLowerCase();
    mesh[i] = (r.mesh || "").toLowerCase();
  }
  state.index = { title, authors, journal, doi, keywords, mesh };
}

// ── Graph build ───────────────────────────────────────────────────────────
function nodeRenderSize(rec) {
  const s = (rec.size || 1) * 0.55;
  return Math.max(0.6, Math.min(8, s));
}

function buildGraph() {
  // Reuse the same Graph instance (and its bound renderer) across dataset
  // switches; clearing drops all nodes and any edge layers.
  // multi:true so the overlay layers can coexist between the same pair of nodes:
  // a citation i→t may appear in the faint citation layer (__cite:) and/or the
  // intra-topic layer (__intra:) and still get a colored selection edge (__sel:)
  // on click. With multi:false the second add between an existing pair throws,
  // which aborted selectNode() before the paper panel opened.
  if (state.graph) state.graph.clear();
  else state.graph = new Graph({ type: "directed", multi: true });

  const nodes = state.nodesData.nodes;
  for (let i = 0; i < nodes.length; i++) {
    const r = nodes[i];
    const size = nodeRenderSize(r);
    state.graph.addNode(String(i), {
      x: r.x,
      y: r.y,
      size,
      color: nodeColor(r), // default color = first grouping
      label: "",
      _data: r,
      _baseSize: size,
    });
  }
}

// ── Sigma setup ───────────────────────────────────────────────────────────
function initSigma() {
  const container = document.getElementById("sigma-container");
  state.renderer = new Sigma(state.graph, container, {
    allowInvalidContainer: true,
    renderEdgeLabels: false,
    enableEdgeEvents: false,
    defaultEdgeColor: "#5a667a",
    labelDensity: 0.02,
    labelGridCellSize: 120,
    labelRenderedSizeThreshold: 14,
    labelColor: { color: "#e6e9ef" },
    labelFont: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    minCameraRatio: 0.03,
    maxCameraRatio: 30,
    nodeReducer,
    edgeReducer,
  });
}

function nodeColor(r) {
  if (state.colorBy === "year") return r.year != null ? yearColor(r.year) : "#888";
  if (state.colorBy === "indegree") return degreeColor(r.indegree || 0);
  if (state.colorBy === "integration") return integrationColor(r);
  const g = groupingConfig(state.colorBy);
  return g ? r[g.colorField] || "#888" : "#888";
}

// True if r is muted by either grouping. A named group is muted by its id; an
// ungrouped paper (no entry in the grouping's data) is muted by NONE_ID.
function groupMutes(key, r) {
  const g = grouping(key);
  const id = r[g.nodeField];
  return g.data[String(id)] ? g.muted.has(id) : g.muted.has(NONE_ID);
}
function nodeHiddenByMute(r) {
  for (const g of activeGroupings()) {
    if (groupMutes(g.key, r)) return true;
  }
  return false;
}

function nodeReducer(node, attrs) {
  const a = Object.assign({}, attrs);
  const r = a._data;

  // Active time snapshot: hide papers not in it (published after the cutoff).
  if (state.snapshot && !state.snapshot.visible.has(node)) {
    a.hidden = true;
    return a;
  }
  if (state.filteredSet && !state.filteredSet.has(node)) {
    a.hidden = true;
    return a;
  }
  if (r.year != null && (r.year < state.yearMin || r.year > state.yearMax)) {
    a.hidden = true;
    return a;
  }
  // Two independent, stacking mutes (named groups + the ungrouped bucket).
  if (nodeHiddenByMute(r)) {
    a.hidden = true;
    return a;
  }

  a.color = nodeColor(r);

  if (state.highlightBridges && r.bridge) {
    a.color = "#ffd76e";
    a.size = a._baseSize * 1.6;
    a.zIndex = 3;
  }

  // Intra-topic islands overlay. Genuine islands (non-main components with >1
  // paper) often overlap the main body in semantic space, so they're always
  // enlarged + raised to pop. Coloring depends on the active mode: when coloring
  // by the semantic grouping all of a topic's papers share one base color, so we
  // apply the island palette to tell components apart (main body = topic color,
  // islands = palette, isolated / non-topic de-emphasized). In any other mode
  // (community / year / citations) the chosen coloring is left intact — islands
  // still stand out by size — so you can e.g. see which community each belongs to.
  if (state.showIntraTopic && state.topicComponents) {
    const i = parseInt(node, 10);
    const tc = state.topicComponents;
    const sem = semanticGrouping();
    if (sem && state.colorBy === sem.key) {
      a.color = intraTopicColor(i, r);
    }
    if (tc.compRank[i] >= 1 && tc.compSizeOf[i] >= 2) {
      a.size = a._baseSize * 1.9;
      a.zIndex = 3;
    }
  }

  if (state.selectedNode !== null) {
    if (node === state.selectedNode) {
      a.size = a._baseSize * 1.6;
      a.zIndex = 4;
      a.label = r.title;
    } else if (state.neighborSet.has(node)) {
      a.zIndex = 3;
    } else {
      a.color = "#2a3140";
      a.size = a._baseSize * 0.5;
      a.zIndex = 0;
      a.label = "";
    }
  }

  if (state.hoveredNode === node) {
    a.size = a._baseSize * 1.4;
    a.zIndex = 5;
  }
  return a;
}

function edgeReducer(edge, attrs) {
  // Static citation layer is hidden unless the global toggle is on.
  if (edge.startsWith("__cite:")) {
    if (!state.showEdges) return { ...attrs, hidden: true };
  } else if (edge.startsWith("__intra:")) {
    if (!state.showIntraTopic) return { ...attrs, hidden: true };
  }
  return attrs;
}

// ── Color modes ───────────────────────────────────────────────────────────
const YEAR_RAMP = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
];

function yearColor(year) {
  const t =
    (year - state.nodesData.year_min) /
    Math.max(1, state.nodesData.year_max - state.nodesData.year_min);
  return rampColor(YEAR_RAMP, clamp01(t));
}
function degreeColor(d) {
  const t = Math.log10(d + 1) / Math.log10(1000);
  return rampColor(YEAR_RAMP, clamp01(t));
}
function rampColor(ramp, t) {
  const x = t * (ramp.length - 1);
  const i = Math.floor(x);
  const frac = x - i;
  const a = ramp[i];
  const b = ramp[Math.min(i + 1, ramp.length - 1)];
  const r = Math.round(a[0] + (b[0] - a[0]) * frac);
  const g = Math.round(a[1] + (b[1] - a[1]) * frac);
  const bl = Math.round(a[2] + (b[2] - a[2]) * frac);
  return `rgb(${r},${g},${bl})`;
}
function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

// ── Floating group labels (switch with the active grouping) ────────────────
// Dynamic mode: with so many topics, showing every label clutters the map.
// We show the largest groups when zoomed out and progressively reveal smaller
// ones as you zoom in, plus always reveal the label of the hovered/selected
// node's group. "Always" mode shows them all.
const LABEL_RATIO_ALL = 0.12; // camera ratio at/below which all labels show
const LABEL_RATIO_FEW = 1.2; // ratio at/above which only the base set shows
const LABEL_BASE_COUNT = 5; // labels always shown when fully zoomed out
// In a time snapshot, only label a topic/community that has at least this many
// papers in that year — keeps early-year maps from being labelled for groups
// that barely exist yet.
const MIN_SNAPSHOT_LABEL = 10;
// ForceAtlas snapshots carry their own per-node sizes from the Gephi export
// (the `size` node attribute, ~8–30, proportional to citation degree that year).
// This factor maps those raw Gephi sizes into the renderer's size units; tune for
// dot density.
const SNAPSHOT_SIZE_SCALE = 0.22;
function snapshotRenderSize(gephiSize) {
  return Math.max(0.6, Math.min(9, (gephiSize || 1) * SNAPSHOT_SIZE_SCALE));
}

function initGroupLabels() {
  // Reposition + decide visibility for the active labels, every frame.
  state.renderer.on("afterRender", positionLabels);
}

function positionLabels() {
  if (!state.activeLabelData) return;

  const visible = visibleLabelIds();
  const { width, height } = state.renderer.getDimensions();
  const margin = 40;

  // When a time snapshot is active, labels follow that snapshot's centroids; a
  // group with no papers in the snapshot has no centroid and is hidden. We also
  // only label groups with a meaningful presence in the snapshot year, so early
  // years aren't cluttered with labels for groups of a handful of papers.
  const snapCentroids = state.snapshot ? state.snapshot.centroids[state.colorBy] : null;
  const snapCounts = state.snapshot ? state.snapshot.counts[state.colorBy] : null;

  for (const gid of Object.keys(state.activeLabelEls)) {
    const el = state.activeLabelEls[gid];
    if (!visible.has(gid)) {
      el.style.display = "none";
      continue;
    }
    if (snapCounts && (snapCounts[gid] || 0) < MIN_SNAPSHOT_LABEL) {
      el.style.display = "none";
      continue;
    }
    const c = state.activeLabelData[gid];
    const centroid = snapCentroids ? snapCentroids[gid] : c.centroid;
    if (!centroid) {
      el.style.display = "none";
      continue;
    }
    const pt = state.renderer.graphToViewport({ x: centroid[0], y: centroid[1] });
    // Cull labels whose centroid is well outside the viewport.
    if (pt.x < -margin || pt.y < -margin || pt.x > width + margin || pt.y > height + margin) {
      el.style.display = "none";
      continue;
    }
    el.style.display = "";
    el.style.transform = `translate(-50%, -50%) translate(${pt.x}px, ${pt.y}px)`;
  }
}

function visibleLabelIds() {
  const order = state.activeLabelOrder;
  if (state.labelMode === "always") return new Set(order);

  const ratio = state.renderer.getCamera().getState().ratio;
  const f = clamp01((LABEL_RATIO_FEW - ratio) / (LABEL_RATIO_FEW - LABEL_RATIO_ALL));
  const count = Math.round(LABEL_BASE_COUNT + f * (order.length - LABEL_BASE_COUNT));
  const visible = new Set(order.slice(0, Math.max(LABEL_BASE_COUNT, count)));

  // Always reveal the group of the hovered/selected node.
  for (const gid of [activeGroupOf(state.hoveredNode), activeGroupOf(state.selectedNode)]) {
    if (gid != null && state.activeLabelData[gid]) visible.add(gid);
  }
  return visible;
}

function activeGroupOf(nodeId) {
  if (nodeId == null) return null;
  const g = groupingConfig(state.colorBy);
  if (!g) return null;
  const r = state.nodesData.nodes[parseInt(nodeId, 10)];
  if (!r) return null;
  return String(r[g.nodeField]);
}

function renderGroupLabels() {
  // The diverging integration scale needs an on-map key; other modes don't.
  const scale = document.getElementById("integration-scale");
  if (scale) scale.hidden = state.colorBy !== "integration";

  const container = document.getElementById("cluster-labels");
  container.innerHTML = "";
  state.activeLabelEls = {};
  state.activeLabelData = null;
  state.activeLabelOrder = [];

  // Continuous color modes (year / citations) have no group labels.
  const g = grouping(state.colorBy);
  if (!g) {
    state.renderer.refresh();
    return;
  }

  state.activeLabelData = g.data;
  state.activeLabelOrder = Object.keys(g.data).sort((a, b) => g.data[b].size - g.data[a].size);
  for (const gid of state.activeLabelOrder) {
    const c = g.data[gid];
    const el = document.createElement("div");
    el.className = "cluster-label";
    el.innerHTML = `<span class="swatch" style="background:${c.color}"></span>${escapeHtml(c.name)}`;
    el.addEventListener("click", (e) => {
      showGroupDetail(g.key, gid);
      e.stopPropagation();
    });
    container.appendChild(el);
    state.activeLabelEls[gid] = el;
  }
  state.renderer.refresh();
}

// ── Hover tooltip ─────────────────────────────────────────────────────────
function initHover() {
  const tt = document.getElementById("tooltip");
  const container = document.getElementById("sigma-container");

  state.renderer.on("enterNode", ({ node }) => {
    state.hoveredNode = node;
    const r = state.graph.getNodeAttribute(node, "_data");
    const auths = (r.authors || "").split("|");
    const shown = auths.slice(0, 3).join(", ");
    const more = auths.length > 3 ? " et al." : "";
    tt.innerHTML =
      `<strong>${escapeHtml(r.title)}</strong>` +
      `<div class="tt-meta">${r.year ?? ""}${r.year ? " &middot; " : ""}${escapeHtml(shown)}${more}</div>`;
    tt.hidden = false;
    state.renderer.refresh();
  });

  state.renderer.on("leaveNode", () => {
    state.hoveredNode = null;
    tt.hidden = true;
    state.renderer.refresh();
  });

  container.addEventListener("mousemove", (e) => {
    if (tt.hidden) return;
    const pad = 14;
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    const r = tt.getBoundingClientRect();
    if (x + r.width > window.innerWidth) x = e.clientX - r.width - pad;
    if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
    tt.style.left = x + "px";
    tt.style.top = y + "px";
  });
}

// ── Selection (click → draw incident edges + open detail) ────────────────
function initSelection() {
  state.renderer.on("clickNode", ({ node }) => selectNode(parseInt(node, 10)));
  state.renderer.on("clickStage", () => {
    clearSelection();
    hideDetail();
  });
}

function selectNode(idx) {
  clearSelectionEdges();
  state.selectedNode = String(idx);
  const nset = new Set();
  nset.add(state.selectedNode);

  for (const t of csrNeighbors(state.outCSR, idx)) {
    const tid = String(t);
    nset.add(tid);
    const k = `__sel:${idx}->${t}`;
    if (!state.graph.hasEdge(k))
      state.graph.addDirectedEdgeWithKey(k, String(idx), tid, { color: "rgba(78,161,255,0.55)", size: 0.7 });
  }
  for (const s of csrNeighbors(state.inCSR, idx)) {
    const sid = String(s);
    nset.add(sid);
    const k = `__sel:${s}->${idx}`;
    if (!state.graph.hasEdge(k))
      state.graph.addDirectedEdgeWithKey(k, sid, String(idx), { color: "rgba(255,158,78,0.45)", size: 0.6 });
  }
  state.neighborSet = nset;
  state.renderer.refresh();
  showPaperDetail(idx);
}

function clearSelectionEdges() {
  const toRemove = [];
  state.graph.forEachEdge((edge) => {
    if (edge.startsWith("__sel:")) toRemove.push(edge);
  });
  for (const e of toRemove) state.graph.dropEdge(e);
}

function clearSelection() {
  clearSelectionEdges();
  state.selectedNode = null;
  state.neighborSet = new Set();
  state.renderer.refresh();
}

// ── Citation edge layer (global toggle) ────────────────────────────────────
function buildCitationLayer() {
  if (state._citeLayerBuilt) return;
  const n = state.outCSR.n;
  for (let i = 0; i < n; i++) {
    const outs = csrNeighbors(state.outCSR, i);
    for (const t of outs) {
      const k = `__cite:${i}->${t}`;
      if (!state.graph.hasEdge(k))
        state.graph.addDirectedEdgeWithKey(k, String(i), String(t), {
          color: "rgba(120,130,150,0.16)",
          size: 0.25,
        });
    }
  }
  state._citeLayerBuilt = true;
}

// ── Intra-topic citation islands ────────────────────────────────────────────
// For each *named* topic (the dataset's semantic grouping), find the connected
// components of the citation subgraph induced on that topic's papers (citations
// treated as undirected, only edges with both endpoints in the topic). >1
// multi-paper component means papers that share a topic but never cite one
// another — disconnected citation communities. Cheap to do at runtime
// (O(nodes + edges)); recomputed when the dataset changes.
const ISLAND_PALETTE = ["#ff5d5d", "#ffb14e", "#fa3e7a", "#9d4edd", "#3ad1ff", "#7cff6b"];

function computeTopicComponents() {
  state.topicComponents = null;
  const sg = semanticGrouping();
  if (!sg) return; // dataset has no semantic grouping → nothing to do
  const field = sg.nodeField;
  const data = sg.data;
  const nodes = state.nodesData.nodes;
  const n = nodes.length;

  // Members of each named topic (skip the unnamed / -1 bucket).
  const members = new Map();
  for (let i = 0; i < n; i++) {
    const t = nodes[i][field];
    if (!data[String(t)]) continue;
    if (!members.has(t)) members.set(t, []);
    members.get(t).push(i);
  }

  const compRank = new Int16Array(n).fill(-1); // rank of node's component (0 = largest)
  const compSizeOf = new Int32Array(n); // size of the node's own component
  const visited = new Uint8Array(n);

  for (const [, idxs] of members) {
    const inTopic = new Set(idxs);
    const comps = [];
    for (const start of idxs) {
      if (visited[start]) continue;
      visited[start] = 1;
      const stack = [start];
      const comp = [];
      while (stack.length) {
        const u = stack.pop();
        comp.push(u);
        for (const v of csrNeighbors(state.outCSR, u)) {
          if (inTopic.has(v) && !visited[v]) { visited[v] = 1; stack.push(v); }
        }
        for (const v of csrNeighbors(state.inCSR, u)) {
          if (inTopic.has(v) && !visited[v]) { visited[v] = 1; stack.push(v); }
        }
      }
      comps.push(comp);
    }
    comps.sort((a, b) => b.length - a.length);
    comps.forEach((comp, rank) => {
      for (const i of comp) { compRank[i] = rank; compSizeOf[i] = comp.length; }
    });
  }

  state.topicComponents = { compRank, compSizeOf };
}

// Color for the intra-topic view: the main citation body keeps the topic color,
// genuine multi-paper islands get vivid palette colors, and isolated singletons
// (papers with no intra-topic citation at all) are de-emphasized.
function intraTopicColor(i, r) {
  const tc = state.topicComponents;
  const rank = tc.compRank[i];
  if (rank < 0) return "#3a4150"; // not in a named topic
  const sg = semanticGrouping();
  if (rank === 0) return (sg && r[sg.colorField]) || "#888"; // largest component = the topic's main body
  if (tc.compSizeOf[i] < 2) return "#5a6170"; // lone disconnected paper
  return ISLAND_PALETTE[(rank - 1) % ISLAND_PALETTE.length];
}

function buildIntraTopicLayer() {
  if (state._intraLayerBuilt) return;
  const sg = semanticGrouping();
  if (!sg) return;
  const field = sg.nodeField;
  const data = sg.data;
  const nodes = state.nodesData.nodes;
  const n = state.outCSR.n;
  for (let i = 0; i < n; i++) {
    const ti = nodes[i][field];
    if (!data[String(ti)]) continue;
    for (const t of csrNeighbors(state.outCSR, i)) {
      if (nodes[t][field] !== ti) continue; // same named topic only
      const k = `__intra:${i}->${t}`;
      if (!state.graph.hasEdge(k))
        state.graph.addDirectedEdgeWithKey(k, String(i), String(t), {
          color: "rgba(150,160,180,0.30)",
          size: 0.4,
        });
    }
  }
  state._intraLayerBuilt = true;
}

// ── Community integration (color mode + detail readout) ─────────────────────
// For the dataset's Leiden citation-community grouping, measure how much each
// community cites outside itself (citations treated as undirected):
//   vol  = total citation degree (in+out) of the community's papers
//   cut  = citations with exactly one endpoint in the community (its boundary)
//   phi  = conductance = cut / min(vol, 2M-vol)        (low = isolated/insular)
//   exp  = config-model expected conductance = (2M-vol)/2M   (~chance)
//   Rext = phi / exp = outward connectivity vs chance  (1.0 = random)
// Plus the partition's global modularity Q. O(nodes + edges); cached on state.
// Coral (isolated) → slate → teal (integrated), diverging around the median phi.
const INTEG_RAMP = [[216, 90, 48], [90, 102, 122], [29, 158, 117]];

function computeCommunityIntegration() {
  state.communityIntegration = null;
  const cg = citationGrouping();
  if (!cg) return;
  const field = cg.nodeField;
  const data = cg.data;
  const nodes = state.nodesData.nodes;
  const n = nodes.length;
  const out = state.outCSR, inn = state.inCSR;
  const M = out.targets.length;       // total directed citations
  const twoM = 2 * M;
  const named = (cid) => data[String(cid)] != null;

  const vol = new Map(), cut = new Map(), internal = new Map();
  const bump = (m, k) => m.set(k, (m.get(k) || 0) + 1);

  for (let i = 0; i < n; i++) {
    const c = nodes[i][field];
    if (!named(c)) continue;
    const deg = (out.offsets[i + 1] - out.offsets[i]) + (inn.offsets[i + 1] - inn.offsets[i]);
    vol.set(c, (vol.get(c) || 0) + deg);
  }
  for (let i = 0; i < n; i++) {
    const ci = nodes[i][field];
    for (const t of csrNeighbors(out, i)) {
      const ct = nodes[t][field];
      if (ci === ct) {
        if (named(ci)) bump(internal, ci);
      } else {
        if (named(ci)) bump(cut, ci);
        if (named(ct)) bump(cut, ct);
      }
    }
  }

  const byId = {};
  const phis = [];
  let Q = 0, internalTotal = 0;
  for (const cid of Object.keys(data)) {
    const c = data[cid].id;
    const v = vol.get(c) || 0, ct = cut.get(c) || 0, ic = internal.get(c) || 0;
    const phi = ct / (Math.min(v, twoM - v) || 1);
    const exp = twoM ? (twoM - v) / twoM : 1;
    byId[c] = { phi, Rext: exp ? phi / exp : 0, cut: ct, vol: v, internal: ic };
    if (v > 0) phis.push(phi);
    Q += (M ? ic / M : 0) - Math.pow(v / (twoM || 1), 2);
    internalTotal += ic;
  }
  phis.sort((a, b) => a - b);
  const lo = phis[0] ?? 0, hi = phis[phis.length - 1] ?? 1;
  const mid = phis.length ? phis[Math.floor(phis.length / 2)] : (lo + hi) / 2;
  state.communityIntegration = { byId, lo, mid, hi, Q, internalShare: M ? internalTotal / M : 0 };
}

// Diverging color for a paper by its community's conductance, centered on the
// field median so the map reads as relatively integrated (teal) vs separate (coral).
function integrationColor(r) {
  const ci = state.communityIntegration;
  const cg = citationGrouping();
  if (!ci || !cg) return "#888";
  const rec = ci.byId[r[cg.nodeField]];
  if (!rec || rec.vol === 0) return "#3a4150"; // ungrouped / no citations
  const { lo, mid, hi } = ci;
  const t = rec.phi <= mid
    ? (mid > lo ? 0.5 * (rec.phi - lo) / (mid - lo) : 0)
    : (hi > mid ? 0.5 + 0.5 * (rec.phi - mid) / (hi - mid) : 1);
  return rampColor(INTEG_RAMP, clamp01(t));
}

// Detail-panel block for a citation community: its integration vs chance.
function integrationBlock(c) {
  if (!state.communityIntegration) computeCommunityIntegration();
  const ci = state.communityIntegration;
  if (!ci) return "";
  const rec = ci.byId[c.id];
  if (!rec || rec.vol === 0) return "";
  const ranked = Object.values(ci.byId).filter((x) => x.vol > 0).map((x) => x.Rext).sort((a, b) => b - a);
  const rank = ranked.indexOf(rec.Rext) + 1;
  const fold = rec.Rext > 0 ? 1 / rec.Rext : 0;
  const verdict = rec.phi >= ci.mid ? "more integrated than most" : "more self-contained than most";
  const pct = (x) => `${Math.round(x * 100)}%`;
  return `
    <h3 title="How much this community cites beyond itself, from the citation graph.">Integration</h3>
    <p class="meta">This community is <strong>${verdict}</strong> — about
       <strong>${fold.toFixed(1)}×</strong> more self-contained than a degree-matched
       random graph (ranked #${rank} of ${ranked.length} by outward connectivity).</p>
    <div class="cmx-stats">
      <span title="Conductance: share of this community's citation links that cross its boundary. Lower = more insular.">Conductance φ: <strong>${rec.phi.toFixed(2)}</strong></span>
      <span title="Boundary citations relative to a degree-matched random graph. 1.0 = chance; below 1 = more self-contained than chance.">Outward vs chance: <strong>${rec.Rext.toFixed(2)}×</strong></span>
    </div>
    <p class="meta">Field-wide: modularity Q = <strong>${ci.Q.toFixed(2)}</strong>; ${pct(ci.internalShare)} of all citations stay within a community.</p>
  `;
}

// ── Detail panel ──────────────────────────────────────────────────────────
function showPaperDetail(idx) {
  const r = state.nodesData.nodes[idx];
  const auths = (r.authors || "").split("|").join(", ");
  const doiHtml = r.doi
    ? `<a href="https://doi.org/${encodeURIComponent(r.doi)}" target="_blank" rel="noopener">Open DOI</a>`
    : "";
  // One tag per grouping the paper belongs to. Each tag is clickable and opens
  // that group's (cluster/topic/community) detail panel.
  const groupTags = activeGroupings()
    .map((g) => {
      const gid = String(r[g.nodeField]);
      const c = state.groupData[g.key][gid];
      return c
        ? `<button type="button" class="cluster-tag cluster-tag-btn" data-group-key="${escapeHtml(g.key)}" data-group-id="${escapeHtml(gid)}" title="Show ${escapeHtml(g.label.toLowerCase())}: ${escapeHtml(c.name)}" style="background:${c.color};color:#0e1116">${escapeHtml(c.name)}</button>`
        : "";
    })
    .join(" ");
  const bridgeBadge = r.bridge
    ? `<span class="cluster-tag" style="background:#ffd76e;color:#0e1116">Bridge paper</span>`
    : "";

  const numOut = state.outCSR.offsets[idx + 1] - state.outCSR.offsets[idx];
  const numIn = state.inCSR.offsets[idx + 1] - state.inCSR.offsets[idx];

  document.getElementById("detail-body").innerHTML = `
    ${groupTags} ${bridgeBadge}
    <h2>${escapeHtml(r.title)}</h2>
    <div class="meta">${escapeHtml(auths)}</div>
    <div class="meta">${r.year ?? ""}${r.journal ? " &middot; " + escapeHtml(r.journal) : ""}</div>
    <div class="meta">${r.indegree || 0} citations &middot; cites ${numOut} &middot; cited by ${numIn}</div>
    <div class="actions">
      ${doiHtml}
      <button id="frame-node">Center on paper</button>
    </div>
    <h3>Abstract</h3>
    <div class="abstract" id="abstract-slot">Loading...</div>
  `;
  document.getElementById("detail").hidden = false;
  document.getElementById("frame-node").addEventListener("click", () => frameNode(idx));
  for (const btn of document.querySelectorAll("#detail-body .cluster-tag-btn")) {
    btn.addEventListener("click", () =>
      showGroupDetail(btn.dataset.groupKey, btn.dataset.groupId));
  }

  loadAbstract(r.id).then((abs) => {
    const slot = document.getElementById("abstract-slot");
    if (slot) slot.textContent = abs || "(no abstract on file)";
  });
}

// Per-topic citation-community composition & connectivity (gemini clusters only;
// produced by topic_community_analysis.py, merged into clusters.json). Shows how
// many citation communities a topic spans and how internally well-connected it is.
function communityMetricsBlock(m) {
  if (!m) return "";
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  // Resolve each community's colour from the active "community" grouping, so the
  // breakdown bar matches the colours used when colouring the map by community.
  const commData = state.groupData.community || {};
  const colorOf = (cid) => (commData[String(cid)] && commData[String(cid)].color) || "#888";

  const segments = (m.community_breakdown || [])
    .map((b) => `<span class="cmx-seg" style="width:${(b.share * 100).toFixed(1)}%;background:${colorOf(b.community)}" title="${escapeHtml(b.name)} — ${pct(b.share)} (${b.count})"></span>`)
    .join("");
  const remainder = 1 - (m.community_breakdown || []).reduce((s, b) => s + b.share, 0);
  const tail = remainder > 0.001
    ? `<span class="cmx-seg" style="width:${(remainder * 100).toFixed(1)}%;background:#3a4150" title="Other communities — ${pct(remainder)}"></span>`
    : "";

  const legend = (m.community_breakdown || [])
    .map((b) => `<li><span class="swatch" style="background:${colorOf(b.community)}"></span>${escapeHtml(b.name)}<span class="cmx-share">${pct(b.share)}</span></li>`)
    .join("");

  // One-line plain-language summary of the two axes (spread × connectedness).
  const single = m.dominant_share >= 0.85;
  const spread = single
    ? "is essentially one citation community"
    : `spans ${m.n_communities} citation communities (~${m.effective_n_communities} effective)`;
  const cohesion = m.internal_edge_ratio >= 0.45 ? "well connected"
    : m.internal_edge_ratio >= 0.25 ? "moderately connected" : "loosely connected";

  // Do the topic's communities cite each other? Only meaningful when the topic
  // actually spans more than one community (integration is null otherwise).
  const integ = m.community_integration;
  const hasInteg = !single && integ != null;
  const integWord = integ >= 0.5 ? "well integrated"
    : integ >= 0.25 ? "partly integrated" : "largely separate";
  const integSentence = hasInteg
    ? ` Its communities are <strong>${integWord}</strong> — ${pct(m.cross_community_edge_ratio)} of within-topic citations bridge communities.`
    : "";
  const integStat = hasInteg
    ? `<span title="Among citations that stay inside the topic, the share linking two different communities — i.e. how much the topic's communities cite each other. The ×chance figure normalizes by random mixing: 1.0 = as mixed as chance, near 0 = siloed.">Cross-community links: <strong>${pct(m.cross_community_edge_ratio)}</strong> (${integ.toFixed(2)}× chance)</span>`
    : "";

  // Recommender: pairs of communities that share this topic but barely cite each
  // other — i.e. likely-relevant literatures that aren't aware of one another.
  const pairs = m.disconnected_pairs || [];
  const gapItems = pairs
    .map((p) => {
      const fold = p.expected_edges > 0 ? (p.expected_edges / Math.max(p.observed_edges, 1)) : 0;
      const foldTxt = p.observed_edges === 0 ? "no citations" : `${fold.toFixed(1)}× fewer than expected`;
      // Optional LLM note: topic-specific summary of each community's papers,
      // then their commonalities and contrasts on this topic.
      const br = p.bridge;
      const bridgeHtml = br
        ? `<details class="cmx-bridge">
             <summary>Compare these communities on this topic <span class="ai-badge" title="LLM-written from the abstracts of representative papers from each community within this topic. Verify against the papers.">AI</span></summary>
             ${br.summary_a ? `<p><strong>${escapeHtml(p.name_a)}:</strong> ${escapeHtml(br.summary_a)}</p>` : ""}
             ${br.summary_b ? `<p><strong>${escapeHtml(p.name_b)}:</strong> ${escapeHtml(br.summary_b)}</p>` : ""}
             ${br.commonalities ? `<p><strong>In common.</strong> ${escapeHtml(br.commonalities)}</p>` : ""}
             ${br.contrasts ? `<p><strong>Contrasts.</strong> ${escapeHtml(br.contrasts)}</p>` : ""}
           </details>`
        : "";
      return `<li>
        <div class="cmx-gap-pair">
          <span class="swatch" style="background:${colorOf(p.community_a)}"></span>${escapeHtml(p.name_a)}
          <span class="cmx-gap-x">⇎</span>
          <span class="swatch" style="background:${colorOf(p.community_b)}"></span>${escapeHtml(p.name_b)}
        </div>
        <div class="sub">${p.observed_edges} citations vs ~${p.expected_edges} expected · ${foldTxt}</div>
        ${bridgeHtml}
      </li>`;
    })
    .join("");
  const more = m.n_disconnected_pairs > pairs.length
    ? `<div class="sub" style="padding-top:4px">+${m.n_disconnected_pairs - pairs.length} more in the data export</div>`
    : "";
  const gapsBlock = gapItems
    ? `<h3 title="Pairs of communities that both work on this topic but cite each other far less than expected — candidate connections between separate literatures.">Citation gaps <span class="cmx-gap-hint">communities not citing each other</span></h3>
       <ul class="top-list cmx-gaps">${gapItems}</ul>${more}`
    : "";

  return `
    <h3>Citation structure</h3>
    <p class="meta">This topic <strong>${spread}</strong>, and is
       <strong>${cohesion}</strong> internally.${integSentence}</p>
    <div class="cmx-bar" title="Citation-community mix within this topic">${segments}${tail}</div>
    <div class="cmx-stats">
      <span title="Distinct citation communities among this topic's papers (effective count weights by size)">Communities: <strong>${m.n_communities}</strong> (~${m.effective_n_communities} eff.)</span>
      <span title="Share of this topic's papers in its single largest citation community">Dominant: <strong>${pct(m.dominant_share)}</strong> ${escapeHtml(m.dominant_community_name || "")}</span>
      <span title="Share of citation edges touching this topic that stay inside it — higher = more self-contained / better connected">Cohesion: <strong>${pct(m.internal_edge_ratio)}</strong></span>
      <span title="Share of the topic's papers in its largest connected citation sub-graph">Connected core: <strong>${pct(m.lcc_fraction)}</strong></span>
      ${integStat}
    </div>
    ${legend ? `<ul class="top-list cmx-legend">${legend}</ul>` : ""}
    ${gapsBlock}
  `;
}

function showGroupDetail(key, gid) {
  const g = grouping(key);
  const c = g.data[gid];
  if (!c) return;
  const label = g.label;
  const isolated = groupIsolated(key, gid);
  // For the semantic (topic) grouping, offer a one-click jump into the
  // intra-topic islands view focused on this topic.
  const islandsBtn = g.semantic
    ? `<button id="show-islands">Highlight citation islands</button>`
    : "";

  const papers = (c.top_papers || [])
    .map((p) => `<li>${escapeHtml(p.title)}<div class="sub">${p.year ?? ""}${p.in_degree ? " &middot; " + p.in_degree + " citations" : ""}</div></li>`)
    .join("");
  const authors = (c.top_authors || [])
    .map((a) => `<li>${escapeHtml(a.name)}<div class="sub">${a.papers ?? ""} papers</div></li>`)
    .join("");
  const keywords = (c.top_keywords || [])
    .map((k) => `<li>${escapeHtml(k.keyword)}<div class="sub">tf-idf ${k.tfidf?.toFixed(3) ?? ""}</div></li>`)
    .join("");
  const wordsBlock = c.top_words
    ? `<h3>Top words</h3><p class="meta">${escapeHtml(c.top_words)}</p>`
    : "";
  const metricsBlock = communityMetricsBlock(c.community_metrics);
  // For a citation community, how integrated vs isolated it is (runtime metric).
  const integBlock = g.citation ? integrationBlock(c) : "";
  // LLM-generated, abstract-grounded summary of the community (optional).
  const themeTags = (c.themes || [])
    .map((t) => `<span class="theme-tag">${escapeHtml(t)}</span>`)
    .join("");
  const summaryBlock = c.summary
    ? `<div class="ai-summary">
         <div class="ai-summary-head">Summary <span class="ai-badge" title="Written by an LLM from the abstracts of representative papers in this group. Verify against the papers below.">AI-generated</span></div>
         <p>${escapeHtml(c.summary)}</p>
         ${themeTags ? `<div class="theme-tags">${themeTags}</div>` : ""}
       </div>`
    : "";

  document.getElementById("detail-body").innerHTML = `
    <span class="cluster-tag" style="background:${c.color};color:#0e1116">${escapeHtml(c.name)}</span>
    <h2>${escapeHtml(c.name)}</h2>
    <div class="meta">${c.size.toLocaleString()} papers in this ${label.toLowerCase()}</div>
    <div class="actions">
      <button id="isolate-group">${isolated ? `Show all ${g.legendLabel.toLowerCase()}` : `Isolate this ${label.toLowerCase()}`}</button>
      <button id="frame-group">Center view</button>
      ${islandsBtn}
    </div>
    ${summaryBlock}
    ${integBlock}
    ${metricsBlock}
    ${wordsBlock}
    ${keywords ? `<h3>Top keywords</h3><ul class="top-list">${keywords}</ul>` : ""}
    ${authors ? `<h3>Top authors</h3><ul class="top-list">${authors}</ul>` : ""}
    ${papers ? `<h3>Top papers</h3><ul class="top-list">${papers}</ul>` : ""}
  `;
  document.getElementById("detail").hidden = false;
  document.getElementById("frame-group").addEventListener("click", () => frameGroup(key, gid));
  document.getElementById("isolate-group").addEventListener("click", () => toggleIsolateGroup(key, gid));
  document.getElementById("show-islands")?.addEventListener("click", () => highlightTopicIslands(key, gid));
}

// One-click: isolate this topic and turn on the intra-topic islands view, so its
// disconnected citation communities (if any) stand out immediately.
function highlightTopicIslands(key, gid) {
  if (!state.topicComponents) computeTopicComponents();
  buildIntraTopicLayer();
  state.showIntraTopic = true;
  const cb = document.getElementById("intra-toggle");
  if (cb) cb.checked = true;
  // Color by the semantic grouping (key is the semantic grouping — this button
  // only shows there) so the full island palette is applied, not just the size
  // highlight. The user can switch "Color by" afterwards to inspect communities.
  state.colorBy = key;
  const sel = document.getElementById("color-by");
  if (sel) sel.value = key;
  renderGroupLabels();
  isolateGroup(key, gid); // refreshes legend + renderer + refilter
  frameGroup(key, gid);
}

function hideDetail() {
  document.getElementById("detail").hidden = true;
}

// ── Camera helpers ────────────────────────────────────────────────────────
function frameNode(idx) {
  const display = state.renderer.getNodeDisplayData(String(idx));
  if (!display) return;
  state.renderer.getCamera().animate({ x: display.x, y: display.y, ratio: 0.15 }, { duration: 600 });
}

function frameGroup(key, gid) {
  const g = grouping(key);
  const c = g.data[gid];
  const cen = groupCentroid(key, gid) || c.centroid;
  const cx = cen[0],
    cy = cen[1];
  let bestIdx = -1;
  let bestDist = Infinity;
  const nodes = state.nodesData.nodes;
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i][g.nodeField] !== c.id) continue;
    // In a snapshot, only members actually present in it can be framed.
    if (state.snapshot && !state.snapshot.visible.has(String(i))) continue;
    // Use live graph coordinates so this works under any active layout/snapshot.
    const dx = state.graph.getNodeAttribute(String(i), "x") - cx,
      dy = state.graph.getNodeAttribute(String(i), "y") - cy;
    const d = dx * dx + dy * dy;
    if (d < bestDist) {
      bestDist = d;
      bestIdx = i;
    }
  }
  if (bestIdx >= 0) frameNode(bestIdx);
}

// ── Isolate / legends ──────────────────────────────────────────────────────
// True when `gid` is the only visible group in its grouping (i.e. the exact
// state isolateGroup() produces): the target isn't muted and every other named
// group + the ungrouped bucket is.
function groupIsolated(key, gid) {
  const g = grouping(key);
  const targetMuted = gid === NONE_ID ? g.muted.has(NONE_ID) : g.muted.has(parseInt(gid, 10));
  if (targetMuted) return false;
  for (const k of Object.keys(g.data)) {
    if (k === String(gid)) continue;
    if (!g.muted.has(parseInt(k, 10))) return false;
  }
  if (String(gid) !== NONE_ID && !g.muted.has(NONE_ID)) return false;
  return true;
}

// Clear every grouping's mutes so all papers show again.
function showAllGroups() {
  for (const g of activeGroupings()) {
    state.muted[g.key].clear();
    refreshLegend(g.key);
  }
  state.renderer.refresh();
  scheduleRefilter();
}

// Detail-panel button: isolate the group if it isn't already, otherwise restore
// the full view. Keeps the button label in sync with the new state.
function toggleIsolateGroup(key, gid) {
  if (groupIsolated(key, gid)) showAllGroups();
  else isolateGroup(key, gid);
  const btn = document.getElementById("isolate-group");
  if (btn) {
    const g = grouping(key);
    btn.textContent = groupIsolated(key, gid)
      ? `Show all ${g.legendLabel.toLowerCase()}`
      : `Isolate this ${g.label.toLowerCase()}`;
  }
}

function isolateGroup(key, gid) {
  const g = grouping(key);
  g.muted.clear();
  // Mute every named group except the target...
  for (const k of Object.keys(g.data)) {
    if (k !== String(gid)) g.muted.add(parseInt(k, 10));
  }
  // ...and the ungrouped bucket too (unless it's the target).
  if (String(gid) !== NONE_ID) g.muted.add(NONE_ID);
  // Mutes stack across groupings, so any other grouping's mutes would subtract
  // from the isolated set and hide part of the target. Clear them: isolating a
  // group selects ALL of its papers and deselects everything else, regardless of
  // what was (de)selected in the other groupings.
  for (const other of activeGroupings()) {
    if (other.key === key) continue;
    state.muted[other.key].clear();
    refreshLegend(other.key);
  }
  refreshLegend(key);
  state.renderer.refresh();
  scheduleRefilter();
}

// ── Controls ──────────────────────────────────────────────────────────────
function initControls() {
  document.getElementById("color-by").addEventListener("change", (e) => {
    state.colorBy = e.target.value;
    if (state.colorBy === "integration" && !state.communityIntegration) computeCommunityIntegration();
    renderGroupLabels();
    state.renderer.refresh();
  });

  document.getElementById("label-mode").addEventListener("change", (e) => {
    state.labelMode = e.target.value;
    state.renderer.refresh();
  });

  document.getElementById("bridge-toggle").addEventListener("change", (e) => {
    state.highlightBridges = e.target.checked;
    state.renderer.refresh();
  });

  document.getElementById("edges-toggle").addEventListener("change", (e) => {
    state.showEdges = e.target.checked;
    if (state.showEdges) buildCitationLayer();
    state.renderer.refresh();
  });

  document.getElementById("intra-toggle").addEventListener("change", (e) => {
    state.showIntraTopic = e.target.checked;
    if (state.showIntraTopic) {
      if (!state.topicComponents) computeTopicComponents();
      buildIntraTopicLayer();
    }
    state.renderer.refresh();
  });

  document.getElementById("detail-close").addEventListener("click", () => {
    hideDetail();
    clearSelection();
  });

  initSearch();
  initControlsToggle();
}

// Build the "Color by" menu from the active dataset's groupings (+ year /
// citations), then point colorBy at the current value.
function buildColorByOptions() {
  const sel = document.getElementById("color-by");
  const opts = activeGroupings().map((g) => ({ value: g.key, label: g.label }));
  opts.push({ value: "year", label: "Year" }, { value: "indegree", label: "Citations" });
  if (citationGrouping()) opts.push({ value: "integration", label: "Integration" });
  sel.innerHTML = opts
    .map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`)
    .join("");
  sel.value = state.colorBy;
}

// Generate one <details> legend block per grouping into #legends.
function buildLegends() {
  const container = document.getElementById("legends");
  container.innerHTML = "";
  state.legendEls = {};
  for (const g of activeGroupings()) {
    const details = document.createElement("details");
    details.className = "legend-wrap";
    const summary = document.createElement("summary");
    summary.textContent = `${g.legendLabel} (click to toggle)`;
    const ul = document.createElement("ul");
    ul.className = "legend-list";
    details.appendChild(summary);
    details.appendChild(ul);
    container.appendChild(details);
    state.legendEls[g.key] = ul;
    buildLegend(g.key);
  }
}

function initSearch() {
  const input = document.getElementById("search");
  const list = document.getElementById("search-results");
  if (!input || !list) return;
  let timer = null;

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(input.value), 120);
  });

  function runSearch(q) {
    q = q.trim().toLowerCase();
    if (q.length < 3) {
      list.hidden = true;
      list.innerHTML = "";
      return;
    }
    const hits = [];
    const nodes = state.nodesData.nodes;
    const idx = state.index;
    for (let i = 0; i < nodes.length && hits.length < 25; i++) {
      if (idx.title[i].includes(q) || idx.authors[i].includes(q) || idx.doi[i].includes(q)) hits.push(i);
    }
    list.innerHTML = hits
      .map((i) => {
        const r = nodes[i];
        const firstAuthor = (r.authors || "").split("|")[0] || "";
        return `<li data-idx="${i}">${escapeHtml(r.title)}<div class="meta">${r.year ?? ""} &middot; ${escapeHtml(firstAuthor)}</div></li>`;
      })
      .join("");
    list.hidden = hits.length === 0;
    for (const li of list.children) {
      li.addEventListener("click", () => {
        const i = parseInt(li.dataset.idx, 10);
        selectNode(i);
        frameNode(i);
        list.hidden = true;
        list.innerHTML = "";
        input.value = "";
      });
    }
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      list.hidden = true;
      list.innerHTML = "";
      input.value = "";
    }
  });
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 150));
}

function updateYear() {
  const ymin = document.getElementById("year-min");
  const ymax = document.getElementById("year-max");
  let lo = parseInt(ymin.value, 10);
  let hi = parseInt(ymax.value, 10);
  if (lo > hi) [lo, hi] = [hi, lo];
  state.yearMin = lo;
  state.yearMax = hi;
  document.getElementById("year-readout").textContent = `${lo}–${hi}`;
  if (state.renderer) state.renderer.refresh();
  scheduleRefilter();
}

// One-time: attach the slider listeners.
function initYearControls() {
  document.getElementById("year-min").addEventListener("input", updateYear);
  document.getElementById("year-max").addEventListener("input", updateYear);
}

// Per-dataset: reset the slider range + values to the dataset's year span.
function resetYearControls() {
  const ymin = document.getElementById("year-min");
  const ymax = document.getElementById("year-max");
  ymin.min = ymax.min = state.nodesData.year_min;
  ymin.max = ymax.max = state.nodesData.year_max;
  ymin.value = state.nodesData.year_min;
  ymax.value = state.nodesData.year_max;
  updateYear();
}

function buildLegend(key) {
  const g = grouping(key);
  const ul = state.legendEls[key];
  ul.innerHTML = "";

  const btnRow = document.createElement("div");
  btnRow.className = "legend-btn-row";
  btnRow.innerHTML = `
    <button data-act="all" type="button">Select All</button>
    <button data-act="none" type="button">Deselect All</button>
  `;
  ul.parentNode.insertBefore(btnRow, ul);

  btnRow.querySelector('[data-act="all"]').addEventListener("click", () => {
    g.muted.clear();
    refreshLegend(key);
    state.renderer.refresh();
    scheduleRefilter();
  });
  btnRow.querySelector('[data-act="none"]').addEventListener("click", () => {
    g.muted.clear();
    for (const cid of Object.keys(g.data)) g.muted.add(parseInt(cid, 10));
    g.muted.add(NONE_ID); // also mute the ungrouped bucket
    refreshLegend(key);
    state.renderer.refresh();
    scheduleRefilter();
  });

  function toggleRow(idValue) {
    if (g.muted.has(idValue)) g.muted.delete(idValue);
    else g.muted.add(idValue);
    refreshLegend(key);
    state.renderer.refresh();
    scheduleRefilter();
  }

  const ids = Object.keys(g.data).sort((a, b) => g.data[b].size - g.data[a].size);
  for (const cid of ids) {
    const c = g.data[cid];
    const li = document.createElement("li");
    li.dataset.cid = cid;
    li.innerHTML = `<span class="swatch" style="background:${c.color}"></span>${escapeHtml(c.name)} <span style="margin-left:auto;color:var(--text-dim)">${c.size}</span>`;
    li.style.display = "flex";
    li.addEventListener("click", () => toggleRow(parseInt(cid, 10)));
    li.addEventListener("dblclick", () => {
      g.muted.clear();
      refreshLegend(key);
      state.renderer.refresh();
      scheduleRefilter();
    });
    ul.appendChild(li);
  }

  // Ungrouped bucket: papers whose group isn't a named entry (e.g. topic -1).
  let noneCount = 0;
  for (const node of state.nodesData.nodes) {
    if (!g.data[String(node[g.nodeField])]) noneCount++;
  }
  if (noneCount > 0) {
    const label = g.noneLabel;
    const li = document.createElement("li");
    li.dataset.cid = NONE_ID;
    li.innerHTML = `<span class="swatch swatch-none"></span><em>${label}</em> <span style="margin-left:auto;color:var(--text-dim)">${noneCount}</span>`;
    li.style.display = "flex";
    li.addEventListener("click", () => toggleRow(NONE_ID));
    ul.appendChild(li);
  }

  refreshLegend(key);
}

function refreshLegend(key) {
  const g = grouping(key);
  const ul = state.legendEls[key];
  for (const li of ul.children) {
    const cid = li.dataset.cid;
    const idValue = cid === NONE_ID ? NONE_ID : parseInt(cid, 10);
    li.classList.toggle("muted", g.muted.has(idValue));
  }
}

// ── Tabs (Graph/Table) ───────────────────────────────────────────────────
function initTabs() {
  const tabGraph = document.getElementById("tab-graph");
  const viewGraph = document.getElementById("view-graph");
  tabGraph.addEventListener("click", () => {
    tabGraph.classList.add("active");
    viewGraph.classList.add("active");
    state.renderer.refresh();
  });
}

function initControlsToggle() {
  const panel = document.getElementById("controls");
  const btn = document.getElementById("controls-toggle");
  if (!panel || !btn) return;
  btn.addEventListener("click", () => {
    const collapsed = panel.classList.toggle("collapsed");
    btn.textContent = collapsed ? "⟩" : "⟨";
    btn.setAttribute("aria-label", collapsed ? "Show controls" : "Hide controls");
    if (state.renderer) state.renderer.refresh();
  });
}

// ── Global filters (v1-like) ─────────────────────────────────────────────
function initGlobalFilters() {
  const elTitle = document.getElementById("filter-title");
  const elAuthor = document.getElementById("filter-author");
  const elAbstract = document.getElementById("filter-abstract");
  const elJournal = document.getElementById("filter-journal");
  const elKeywords = document.getElementById("filter-keywords");
  const btn = document.getElementById("apply-filters");

  function readFiltersFromUI() {
    state.filters.title = elTitle?.value || "";
    state.filters.author = elAuthor?.value || "";
    state.filters.abstract = elAbstract?.value || "";
    state.filters.journal = elJournal?.value || "";
    state.filters.keywords = elKeywords?.value || "";
  }

  async function run() {
    btn.disabled = true;
    const oldTxt = btn.textContent;
    btn.textContent = "Filtering...";
    try {
      readFiltersFromUI();
      await applyGlobalFilters();
    } finally {
      btn.disabled = false;
      btn.textContent = oldTxt;
    }
  }

  btn.addEventListener("click", run);

  const inputs = [elTitle, elAuthor, elAbstract, elJournal, elKeywords].filter(Boolean);
  for (const input of inputs) {
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      run();
    });
  }
}

function splitCommaQueries(s) {
  return s
    .split(",")
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}
function matchesAll(pipeLc, queries) {
  for (const q of queries) if (!pipeLc.includes(q)) return false;
  return true;
}

async function ensureAbstractsLoadedIfNeeded() {
  if (state.filters.abstract.trim() && !state.abstracts) await loadAbstract("n2");
}

async function applyGlobalFilters() {
  await ensureAbstractsLoadedIfNeeded();

  const f = state.filters;
  const qTitle = f.title.trim().toLowerCase();
  const qJournal = f.journal.trim().toLowerCase();
  const qAbstract = f.abstract.trim().toLowerCase();
  const authorQs = splitCommaQueries(f.author);
  const keywordQs = splitCommaQueries(f.keywords);
  const meshQs = splitCommaQueries(f.mesh);

  const lo = state.yearMin;
  const hi = state.yearMax;
  const idx = state.index;
  const nodes = state.nodesData.nodes;
  const out = new Set();

  for (let i = 0; i < nodes.length; i++) {
    const r = nodes[i];
    if (state.snapshot && !state.snapshot.visible.has(String(i))) continue;
    if (r.year != null && (r.year < lo || r.year > hi)) continue;
    if (nodeHiddenByMute(r)) continue;
    if (qTitle && !idx.title[i].includes(qTitle)) continue;
    if (qJournal && !idx.journal[i].includes(qJournal)) continue;
    if (authorQs.length && !matchesAll(idx.authors[i], authorQs)) continue;
    if (keywordQs.length && !matchesAll(idx.keywords[i], keywordQs)) continue;
    if (meshQs.length && !matchesAll(idx.mesh[i], meshQs)) continue;
    if (qAbstract) {
      const abs = (state.abstracts?.[r.id] || "").toLowerCase();
      if (!abs.includes(qAbstract)) continue;
    }
    out.add(String(i));
  }

  state.filteredSet = out;
  updateSelectedCount();
  state.renderer.refresh();
}

function scheduleRefilter() {
  clearTimeout(state._refilterTimer);
  state._refilterTimer = setTimeout(() => {
    if (state.filteredSet) applyGlobalFilters();
    else updateSelectedCount();
  }, 90);
}

function updateSelectedCount() {
  const el = document.getElementById("selected-count");
  if (!el) return;
  if (!state.filteredSet) {
    let c = 0;
    const nodes = state.nodesData.nodes;
    for (let i = 0; i < nodes.length; i++) {
      const r = nodes[i];
      if (state.snapshot && !state.snapshot.visible.has(String(i))) continue;
      if (r.year != null && (r.year < state.yearMin || r.year > state.yearMax)) continue;
      if (nodeHiddenByMute(r)) continue;
      c++;
    }
    el.textContent = `Nodes selected: ${c.toLocaleString()}`;
    return;
  }
  el.textContent = `Nodes selected: ${state.filteredSet.size.toLocaleString()}`;
}

// ── Shift key tracking ─────────────────────────────────────────────────────
function initShiftTracking() {
  window.addEventListener("keydown", (e) => {
    if (e.key === "Shift") state.shiftDown = true;
  });
  window.addEventListener("keyup", (e) => {
    if (e.key === "Shift") state.shiftDown = false;
  });
}

// ── Lazy-loaded abstracts ────────────────────────────────────────────────
function loadAbstract(nodeId) {
  if (state.abstracts) return Promise.resolve(state.abstracts[nodeId] || "");
  if (!state.abstractsPromise) {
    state.abstractsPromise = fetch(`${DATASETS[state.dataset].dir}/abstracts.json`)
      .then((r) => r.json())
      .then((obj) => {
        state.abstracts = obj;
        return obj;
      });
  }
  return state.abstractsPromise.then((obj) => obj[nodeId] || "");
}

// ── Utils ─────────────────────────────────────────────────────────────────
function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
