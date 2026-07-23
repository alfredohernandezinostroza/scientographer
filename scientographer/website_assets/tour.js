// Guided tour: a self-contained walkthrough that spotlights each control in
// turn with a short explanation. It only reads the DOM (and clicks the controls
// toggle to expand the panel); it does not touch main.js state, so it stays in
// sync no matter which dataset is loaded. Launch from the floating "Guided tour"
// button (#start-tour); auto-runs once on first visit (localStorage flag).
// The × in the popover corner (or Esc / Skip) dismisses the whole tour.

const SEEN_KEY = "mlsm_tour_seen_v1";

// Each step spotlights a target and shows a popover.
//   target  — CSS selector, or array of selectors whose rects are unioned.
//             Omit for a centered step (no spotlight).
//   center  — true: keep the popover centered even though there's a target.
//   row     — true: expand each target up to its enclosing `.control-row` so the
//             label is included in the spotlight.
//   title / body — popover copy (body is HTML).
// Steps whose target is missing or hidden are skipped automatically, so this one
// list works for every dataset (e.g. the Time control only appears for datasets
// that ship snapshots).
const STEPS = [
  {
    title: "Welcome to the Semantic Map",
    body:
      "This is an interactive map of <strong>~14,500 motor-learning papers</strong>. " +
      "Every dot is one paper; dot size is how often it's cited. Nearby dots are " +
      "related — by text content or by citations, depending on the layout.<br><br>" +
      "This quick tour walks through everything you can do. Use <em>Next</em> / " +
      "<em>Back</em>, or <em>Skip</em> to leave at any time.",
  },
  {
    target: "#sigma-container",
    center: true,
    title: "Navigating the map",
    body:
      "<strong>Drag</strong> to pan, <strong>scroll</strong> to zoom. " +
      "<strong>Hover</strong> a dot to see its title, year and authors. " +
      "<strong>Click</strong> a dot to draw its citation links and open the detail " +
      "panel on the right.",
  },
  {
    target: "#controls-toggle",
    title: "The controls panel",
    body:
      "Everything on the left is your control panel. This button " +
      "<strong>collapses or expands</strong> it whenever you want more room to " +
      "explore the map.",
  },
  {
    target: "#search",
    title: "Search",
    body:
      "Jump straight to a paper by <strong>title, author or DOI</strong>. Pick a " +
      "result to fly to it on the map and open its details.",
  },
  {
    target: ["#filter-title", "#filter-keywords", "#apply-filters"],
    title: "Filter the field",
    body:
      "Narrow the map to a subset of papers — filter by <strong>publication, " +
      "author(s), abstract text, journal, or keywords</strong>, then press " +
      "<strong>Filter</strong>. The <em>Nodes selected</em> count shows how many " +
      "papers match.",
  },
  {
    target: ["#year-min", "#year-max"],
    row: true,
    title: "Year range",
    body:
      "Restrict the visible papers to a <strong>publication-year window</strong> " +
      "with the two sliders.",
  },
  {
    target: "#snapshot-select",
    row: true,
    title: "Time travel",
    body:
      "Re-lay-out the field as it stood <strong>up to a past year</strong> " +
      "(&le; 1980 / 1990 / 2000 / 2010), or <em>All</em> for today. Only the papers " +
      "and citations that existed by then are placed — colours and topics stay " +
      "current, so you can watch the field grow.",
  },
  {
    target: "#color-by",
    row: true,
    title: "Colour by",
    body:
      "Recolour the dots by <strong>semantic topic/cluster</strong>, Leiden " +
      "<strong>citation community</strong>, <strong>year</strong>, or " +
      "<strong>citation count</strong>.",
  },
  {
    target: "#label-mode",
    row: true,
    title: "Labels",
    body:
      "Choose how the floating topic/community labels behave: " +
      "<strong>Dynamic</strong> reveals them as you zoom and hover, or keep them " +
      "<strong>Always visible</strong>.",
  },
  {
    target: "#bridge-toggle",
    row: true,
    title: "Bridge papers",
    body:
      "Highlight <strong>bridge papers</strong> — the ones that link otherwise " +
      "separate parts of the map.",
  },
  {
    target: "#edges-toggle",
    row: true,
    title: "Citation edges",
    body:
      "Overlay the actual <strong>citation links</strong>. On the embedding maps, " +
      "long edges connect papers cited together but far apart in content — i.e. " +
      "where the citation graph crosses the semantic layout.",
  },
  {
    target: "#intra-toggle",
    row: true,
    title: "Intra-topic citation islands",
    body:
      "Reveal where a single topic splits into separate, <strong>non-citing " +
      "groups</strong>. Within each topic the citation links are broken into " +
      "connected components, and every extra &ldquo;island&rdquo; is enlarged so " +
      "it stands out — papers sharing a subject but forming separate literatures.",
  },
  {
    target: "#legends",
    title: "Legend & toggles",
    body:
      "The legend lists every topic and community with its colour. " +
      "<strong>Click an entry</strong> to mute/unmute that group on the map, so you " +
      "can isolate the parts you care about.",
  },
  {
    // Citation view only: nudge the user toward the topic layouts (where the
    // topic-detail step below lives).
    view: "citation",
    target: "#dataset-select",
    row: true,
    title: "Also try the topic view",
    body:
      "You're on the <strong>citation-graph</strong> layout. Also try a " +
      "<strong>topic view</strong> (<strong>Gemini</strong> or " +
      "<strong>Specter</strong>) from the <strong>Dataset</strong> menu: papers are " +
      "placed by <strong>text content</strong> and grouped into topics, with extra " +
      "topic tools like clickable topic labels.",
  },
  {
    // Topic views only: the topic labels / detail panel don't apply in the
    // citation layout.
    view: "topic",
    target: "#cluster-labels",
    center: true,
    title: "Topic detail & citation gaps",
    body:
      "<strong>Click a topic label</strong> to open its panel — how many citation " +
      "communities the topic spans, its internal cohesion, and a " +
      "<strong>Citation gaps</strong> list of communities that share the topic but " +
      "barely cite each other.",
  },
  {
    target: ".about",
    title: "That's the tour!",
    body:
      "Open <strong>About this map</strong> any time for a fuller explanation of the " +
      "layouts and overlays. Replay this tour from the <strong>Guided tour</strong> " +
      "button in the corner. Happy exploring!",
  },
];

const state = { steps: null, idx: 0, els: null, isFull: false };

// ── DOM helpers ─────────────────────────────────────────────────────────────
function expandControls() {
  const panel = document.getElementById("controls");
  if (panel && panel.classList.contains("collapsed")) {
    document.getElementById("controls-toggle")?.click();
  }
}

// Resolve a step to the list of live elements it spotlights (after row-expand).
function resolveTargets(step) {
  if (!step.target) return [];
  const sels = Array.isArray(step.target) ? step.target : [step.target];
  const out = [];
  for (const sel of sels) {
    let el = document.querySelector(sel);
    if (!el) continue;
    if (step.row) el = el.closest(".control-row") || el;
    if (!out.includes(el)) out.push(el);
  }
  return out;
}

function isUsable(el) {
  if (!el) return false;
  if (el.hasAttribute("hidden")) return false;
  if (el.offsetParent === null && el !== document.body) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

// "citation" = the ForceAtlas citation-graph layout; "topic" = the
// text-embedding layouts (Gemini / Specter).
function currentView() {
  const d = document.getElementById("dataset-select")?.value;
  return d === "gemini" || d === "specter" ? "topic" : "citation";
}

function stepIsAvailable(step) {
  if (step.view && step.view !== currentView()) return false;
  if (!step.target) return true;
  return resolveTargets(step).some(isUsable);
}

function unionRect(els) {
  let l = Infinity,
    t = Infinity,
    r = -Infinity,
    b = -Infinity;
  for (const el of els) {
    const rect = el.getBoundingClientRect();
    l = Math.min(l, rect.left);
    t = Math.min(t, rect.top);
    r = Math.max(r, rect.right);
    b = Math.max(b, rect.bottom);
  }
  return { left: l, top: t, right: r, bottom: b, width: r - l, height: b - t };
}

// ── Overlay construction ─────────────────────────────────────────────────────
function buildOverlay() {
  const root = document.createElement("div");
  root.id = "tour-root";
  root.innerHTML = `
    <div id="tour-blocker"></div>
    <div id="tour-spotlight" hidden></div>
    <div id="tour-popover" role="dialog" aria-modal="true">
      <button id="tour-close" type="button" aria-label="Skip the tour">&times;</button>
      <div id="tour-title"></div>
      <div id="tour-body"></div>
      <div id="tour-footer">
        <span id="tour-progress"></span>
        <span class="tour-spacer"></span>
        <button id="tour-skip" type="button" class="tour-ghost">Skip tour</button>
        <button id="tour-back" type="button" class="tour-ghost">Back</button>
        <button id="tour-next" type="button" class="tour-primary">Next</button>
      </div>
    </div>`;
  document.body.appendChild(root);

  root.querySelector("#tour-close").addEventListener("click", endTour);
  root.querySelector("#tour-skip").addEventListener("click", endTour);
  root.querySelector("#tour-back").addEventListener("click", () => go(-1));
  root.querySelector("#tour-next").addEventListener("click", () => go(1));

  return {
    root,
    spotlight: root.querySelector("#tour-spotlight"),
    popover: root.querySelector("#tour-popover"),
    title: root.querySelector("#tour-title"),
    body: root.querySelector("#tour-body"),
    progress: root.querySelector("#tour-progress"),
    skip: root.querySelector("#tour-skip"),
    back: root.querySelector("#tour-back"),
    next: root.querySelector("#tour-next"),
  };
}

// Place the popover near the spotlight rect: prefer the right side (the controls
// panel hugs the left edge), fall back to left, then centered.
function placePopover(rect) {
  const pop = state.els.popover;
  pop.classList.remove("tour-centered");
  const vw = window.innerWidth,
    vh = window.innerHeight;
  const pw = pop.offsetWidth,
    ph = pop.offsetHeight;
  const gap = 16,
    margin = 12;

  let left, top;
  if (rect.right + gap + pw + margin <= vw) {
    left = rect.right + gap; // right of target
  } else if (rect.left - gap - pw - margin >= 0) {
    left = rect.left - gap - pw; // left of target
  } else {
    left = (vw - pw) / 2; // horizontally centered
  }
  top = rect.top + rect.height / 2 - ph / 2;
  top = Math.max(margin, Math.min(top, vh - ph - margin));
  left = Math.max(margin, Math.min(left, vw - pw - margin));
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

function centerPopover() {
  const pop = state.els.popover;
  pop.classList.add("tour-centered");
  pop.style.left = "";
  pop.style.top = "";
}

function renderStep() {
  const els = state.els;
  const step = state.steps[state.idx];
  const single = state.steps.length === 1;
  const last = state.idx === state.steps.length - 1;

  els.title.innerHTML = step.title;
  els.body.innerHTML = step.body;
  els.progress.textContent = single ? "" : `${state.idx + 1} / ${state.steps.length}`;
  els.skip.hidden = single;
  els.back.hidden = single;
  els.back.disabled = state.idx === 0;
  els.next.textContent = single ? "Got it" : last ? "Done" : "Next";

  const rawTargets = resolveTargets(step);
  const inPanel = rawTargets.some((el) => el.closest("#controls"));
  if (inPanel) expandControls();

  const targets = rawTargets.filter(isUsable);
  if (targets[0]) targets[0].scrollIntoView({ block: "center", inline: "nearest" });

  // Measure after layout settles (scroll / panel expand).
  requestAnimationFrame(() => {
    if (!step.target || step.center || targets.length === 0) {
      els.spotlight.hidden = true;
      centerPopover();
      return;
    }
    const rect = unionRect(targets);
    const pad = 6;
    els.spotlight.hidden = false;
    els.spotlight.style.left = `${rect.left - pad}px`;
    els.spotlight.style.top = `${rect.top - pad}px`;
    els.spotlight.style.width = `${rect.width + pad * 2}px`;
    els.spotlight.style.height = `${rect.height + pad * 2}px`;
    placePopover({
      left: rect.left - pad,
      top: rect.top - pad,
      right: rect.right + pad,
      width: rect.width + pad * 2,
      height: rect.height + pad * 2,
    });
  });
}

function go(delta) {
  const ni = state.idx + delta;
  if (ni < 0) return;
  if (ni >= state.steps.length) {
    endTour();
    return;
  }
  state.idx = ni;
  renderStep();
}

function onKey(e) {
  if (e.key === "Escape") endTour();
  else if (e.key === "ArrowRight") go(1);
  else if (e.key === "ArrowLeft") go(-1);
}

let _reflow;
function onReflow() {
  clearTimeout(_reflow);
  _reflow = setTimeout(renderStep, 120);
}

// Run an arbitrary ordered list of step objects through the overlay.
function startSteps(steps, { isFull = false } = {}) {
  if (state.els) return; // a run is already active
  expandControls();
  const usable = steps.filter(stepIsAvailable);
  if (usable.length === 0) return;
  state.steps = usable;
  state.idx = 0;
  state.isFull = isFull;
  state.els = buildOverlay();
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", onReflow);
  window.addEventListener("scroll", onReflow, true);
  renderStep();
}

function startTour() {
  startSteps(STEPS, { isFull: true });
}

function endTour() {
  if (state.isFull) {
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch (_) {}
  }
  document.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", onReflow);
  window.removeEventListener("scroll", onReflow, true);
  state.els?.root.remove();
  state.els = null;
  state.steps = null;
  state.isFull = false;
}

// ── Boot ──────────────────────────────────────────────────────────────────
document.getElementById("start-tour")?.addEventListener("click", startTour);

// Auto-run once on first visit, after the map has had a chance to load.
let _seen = "1";
try {
  _seen = localStorage.getItem(SEEN_KEY);
} catch (_) {}
if (!_seen) {
  window.addEventListener("load", () => setTimeout(startTour, 900));
}
