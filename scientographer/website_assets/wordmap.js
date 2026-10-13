// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// Word map tab: where words sit on every view of the map, computed in the browser.
//
// Ported from the CitationMap's compare.html / compare.js (word_heatmap.py's
// maths): match words in each paper's text, drop the matching papers onto a
// layout, blur with a Gaussian (a fixed-bandwidth KDE) and normalise to the
// panel's own peak. Rows are the site's views (views.json: the citation layout
// plus any extra layouts, e.g. embedding maps), columns the text searched.
//
// Views can hold different papers (an embedding view only has the papers with
// embeddings), so each view keeps its own paper list; the text is matched once
// per paper id and every view looks its papers up.
//
// The tab follows the map's community resolution and its "Well-connected
// papers only" filter (and its own controls for both set the map's): with the
// filter on, only the papers the Connectivity Modifier keeps at that resolution
// are counted, smoothed and drawn.

import { MATCH_MODES, buildMatcher, parseTerms } from "./textmatch.js";

const FIELDS = [
  { key: "abstract", label: "Abstract" },
  { key: "keywords", label: "Keywords" },
  { key: "both", label: "Abstract + keywords" },
];

const REDS = ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a",
              "#ef3b2c", "#cb181d", "#a50f15", "#67000d"];
const FADE = 0.15;          // bottom of the ramp fades to transparent
const GRID_W = 300;         // KDE raster width in cells; height follows the aspect
const EXPORT_W = 2800;      // pixel width of a saved PNG
const EXPORT_GRID = 1000;   // KDE raster width used for exports
const EXPORT_MARGIN = 0.10; // export frame padding, as a share of the layout's span

// Screen sits on the dark page; an exported PNG has a transparent background
// and is usually viewed on white, so it uses a darker, denser dot.
const DOT_COLOR = "rgba(230, 233, 239, 0.22)";
const DOT_COLOR_EXPORT = "rgba(150, 150, 150, 0.85)";
const RING_COLOR = "#67000d";

const state = {
  started: false,
  layouts: [],      // [{ key, label, note, ids, xs, ys, comm, groups, extent, grid }]
  texts: null,      // id -> { abstract, keywords }
  token: 0,         // cancels superseded recomputes
  last: null,       // per layout + field: counts and weights of the current view
};

const el = (id) => document.getElementById(id);

// ── Colour ramp ───────────────────────────────────────────────────────────
const LUT = (() => {
  const stops = REDS.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
  const lut = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    const p = t * (stops.length - 1);
    const a = Math.min(Math.floor(p), stops.length - 2);
    const f = p - a;
    for (let c = 0; c < 3; c++) lut[i * 4 + c] = stops[a][c] + (stops[a + 1][c] - stops[a][c]) * f;
    lut[i * 4 + 3] = 255 * Math.min(1, t / FADE);
  }
  return lut;
})();

// ── Loading ───────────────────────────────────────────────────────────────
async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

async function views() {
  try {
    const manifest = await fetchJson("views.json");
    if (Array.isArray(manifest.views) && manifest.views.length) {
      return { list: manifest.views, shared: manifest.shared_dir || manifest.views[0].dir };
    }
  } catch {
    // a single-view site has no views.json
  }
  return { list: [{ key: "network", label: "Citation network", dir: "network_data" }], shared: "network_data" };
}

function geometry(xs, ys) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < xs.length; i++) {
    if (xs[i] < x0) x0 = xs[i];
    if (xs[i] > x1) x1 = xs[i];
    if (ys[i] < y0) y0 = ys[i];
    if (ys[i] > y1) y1 = ys[i];
  }
  const pad = 0.03 * Math.max(x1 - x0, y1 - y0);
  const extent = [x0 - pad, x1 + pad, y0 - pad, y1 + pad];
  const gx = GRID_W;
  const gy = Math.max(1, Math.round(gx * (extent[3] - extent[2]) / (extent[1] - extent[0])));
  return { extent, grid: { gx, gy, cell: (extent[1] - extent[0]) / gx } };
}

function currentResolution() {
  return el("wm-resolution")?.value || window.scientographer?.communityResolution?.() || null;
}

// Which of a view's papers are drawn: those the Connectivity Modifier keeps at
// the selected resolution when the filter is on (null = every paper).
function keptMask(L) {
  if (!el("wm-well-connected")?.checked) return null;
  const resolution = currentResolution();
  const encoded = L.wellConnected?.resolutions?.[resolution];
  if (!encoded) return null;
  if (!L.masks[resolution]) {
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    const mask = new Uint8Array(L.n);
    for (let i = 0; i < L.n; i++) mask[i] = (bytes[i >> 3] >> (i & 7)) & 1;
    L.masks[resolution] = mask;
  }
  return L.masks[resolution];
}

async function load() {
  const status = el("wm-status");
  status.className = "";
  status.textContent = "Loading the views…";
  const { list, shared } = await views();
  const texts = new Map();
  state.layouts = await Promise.all(list.map(async (v) => {
    const [payload, communities, wellConnected] = await Promise.all([
      fetchJson(`${v.dir}/nodes.json`),
      fetchJson(`${v.dir}/communities_by_resolution.json`).catch(() => null),
      fetchJson(`${v.dir}/well_connected.json`).catch(() => null),
    ]);
    const nodes = payload.nodes;
    const n = nodes.length;
    const xs = new Float64Array(n), ys = new Float64Array(n);
    const ids = new Array(n), communitiesById = new Array(n);
    for (let i = 0; i < n; i++) {
      const r = nodes[i];
      xs[i] = r.x; ys[i] = r.y; ids[i] = r.id;
      communitiesById[i] = r.communities || {};
      if (!texts.has(r.id)) texts.set(r.id, { abstract: "", keywords: r.keywords || "" });
    }
    return {
      key: v.key, label: v.label, note: v.subtitle || "", ids, xs, ys, n,
      communities: communitiesById, legend: communities, wellConnected, masks: {}, ...geometry(xs, ys),
    };
  }));
  status.textContent = "Loading the abstracts…";
  const abstracts = await fetchJson(`${shared}/abstracts.json`);
  for (const [id, text] of texts) text.abstract = abstracts[id] || "";
  state.texts = texts;
  status.textContent = "";
}

// ── KDE ───────────────────────────────────────────────────────────────────
function histogram(L, weights, grid, extent) {
  const { gx, gy } = grid;
  const [x0, x1, y0, y1] = extent;
  const h = new Float32Array(gx * gy);
  const sx = gx / (x1 - x0), sy = gy / (y1 - y0);
  const kept = keptMask(L);
  for (let i = 0; i < L.n; i++) {
    if (kept && !kept[i]) continue;
    const w = weights ? weights[i] : 1;
    if (!w) continue;
    const cx = Math.min(gx - 1, Math.max(0, (L.xs[i] - x0) * sx | 0));
    const cy = Math.min(gy - 1, Math.max(0, (L.ys[i] - y0) * sy | 0));
    h[cy * gx + cx] += w;
  }
  return h;
}

// Separable Gaussian blur, sigma in cells.
function blur(src, gx, gy, sigma) {
  if (sigma < 0.3) return src;
  const r = Math.max(1, Math.ceil(3 * sigma));
  const k = new Float32Array(2 * r + 1);
  let sum = 0;
  for (let i = -r; i <= r; i++) {
    k[i + r] = Math.exp(-(i * i) / (2 * sigma * sigma));
    sum += k[i + r];
  }
  for (let i = 0; i < k.length; i++) k[i] /= sum;
  const tmp = new Float32Array(gx * gy);
  for (let y = 0; y < gy; y++) {
    for (let x = 0; x < gx; x++) {
      let acc = 0;
      for (let i = -r; i <= r; i++) {
        const xx = x + i;
        if (xx >= 0 && xx < gx) acc += src[y * gx + xx] * k[i + r];
      }
      tmp[y * gx + x] = acc;
    }
  }
  const out = new Float32Array(gx * gy);
  for (let y = 0; y < gy; y++) {
    for (let x = 0; x < gx; x++) {
      let acc = 0;
      for (let i = -r; i <= r; i++) {
        const yy = y + i;
        if (yy >= 0 && yy < gy) acc += tmp[yy * gx + x] * k[i + r];
      }
      out[y * gx + x] = acc;
    }
  }
  return out;
}

// Bandwidth is a share of the layout's own span, so layouts on different
// coordinate scales are smoothed comparably.
function bandwidth(L) {
  const share = Number(el("wm-bw").value) / 1000; // slider 5..60 -> 0.5%..6%
  return share * Math.max(L.extent[1] - L.extent[0], L.extent[3] - L.extent[2]);
}

function density(L, weights, grid, extent, mode, sigma) {
  const { gx, gy } = grid;
  let f = blur(histogram(L, weights, grid, extent), gx, gy, sigma);
  if (mode === "enrichment") {
    const background = blur(histogram(L, null, grid, extent), gx, gy, sigma);
    let bgMax = 0;
    for (let i = 0; i < background.length; i++) bgMax = Math.max(bgMax, background[i]);
    const floor = bgMax * 0.03;
    const out = new Float32Array(f.length);
    for (let i = 0; i < f.length; i++) {
      if (background[i] > 0) out[i] = (f[i] / background[i]) * Math.min(1, background[i] / floor);
    }
    f = out;
  }
  let max = 0;
  for (let i = 0; i < f.length; i++) max = Math.max(max, f[i]);
  if (max > 0) for (let i = 0; i < f.length; i++) f[i] /= max;
  return f;
}

// ── Counting + weighting ──────────────────────────────────────────────────
// Matches per paper id for the abstract and keyword fields, counted once for
// all views.
function countById(matcher) {
  const abstract = new Map(), keywords = new Map();
  for (const [id, text] of state.texts) {
    const a = text.abstract ? text.abstract.match(matcher.re) : null;
    const k = text.keywords ? text.keywords.match(matcher.re) : null;
    if (a) abstract.set(id, a.length);
    if (k) keywords.set(id, k.length);
  }
  return { abstract, keywords };
}

const wordCounts = new Map();
function wordsIn(id, field) {
  const key = `${field}:${id}`;
  if (!wordCounts.has(key)) {
    const text = state.texts.get(id) || {};
    const count = (t) => (t ? t.split(/\s+/).length : 0);
    wordCounts.set(key, field === "both" ? count(text.abstract) + count(text.keywords) : count(text[field]));
  }
  return wordCounts.get(key);
}

function weightsFor(L, byId, field, how) {
  const counts = new Int32Array(L.n);
  const weights = new Float32Array(L.n);
  const kept = keptMask(L);
  for (let i = 0; i < L.n; i++) {
    if (kept && !kept[i]) continue;
    const id = L.ids[i];
    const c = field === "both"
      ? (byId.abstract.get(id) || 0) + (byId.keywords.get(id) || 0)
      : (byId[field].get(id) || 0);
    if (!c) continue;
    counts[i] = c;
    if (how === "presence") weights[i] = 1;
    else if (how === "count") weights[i] = c;
    else if (how === "log") weights[i] = Math.log1p(c);
    else weights[i] = (c / Math.max(1, wordsIn(id, field))) * 1000;
  }
  return { counts, weights };
}

// ── Painting ──────────────────────────────────────────────────────────────
function paint(canvas, L, f, counts, grid, extent, pxW, exporting) {
  const { gx, gy } = grid;
  const [x0, x1, y0, y1] = extent;
  canvas.width = Math.round(pxW);
  canvas.height = Math.round(pxW * (gy / gx));
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const sx = canvas.width / (x1 - x0), sy = canvas.height / (y1 - y0);
  const k = canvas.width / 420; // mark scale, ~1 at screen size

  if (el("wm-dots").checked) {
    ctx.fillStyle = exporting ? DOT_COLOR_EXPORT : DOT_COLOR;
    const size = Math.max(1, 1.1 * k);
    const kept = keptMask(L);
    for (let i = 0; i < L.n; i++) {
      if (kept && !kept[i]) continue;
      ctx.fillRect((L.xs[i] - x0) * sx, canvas.height - (L.ys[i] - y0) * sy, size, size);
    }
  }

  const img = new ImageData(gx, gy);
  for (let y = 0; y < gy; y++) {
    for (let x = 0; x < gx; x++) {
      const v = f[(gy - 1 - y) * gx + x]; // data y grows up, image y down
      const t = Math.max(0, Math.min(255, Math.round(v * 255)));
      const o = (y * gx + x) * 4;
      img.data[o] = LUT[t * 4];
      img.data[o + 1] = LUT[t * 4 + 1];
      img.data[o + 2] = LUT[t * 4 + 2];
      img.data[o + 3] = LUT[t * 4 + 3];
    }
  }
  const off = document.createElement("canvas");
  off.width = gx;
  off.height = gy;
  off.getContext("2d").putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(off, 0, 0, gx, gy, 0, 0, canvas.width, canvas.height);

  if (counts && el("wm-rings").checked) {
    const r = Math.max(1.5, 2.2 * k);
    ctx.strokeStyle = RING_COLOR;
    ctx.lineWidth = Math.max(0.8, 0.9 * k);
    ctx.beginPath();
    for (let i = 0; i < L.n; i++) {
      if (!counts[i]) continue;
      const px = (L.xs[i] - x0) * sx, py = canvas.height - (L.ys[i] - y0) * sy;
      ctx.moveTo(px + r, py);
      ctx.arc(px, py, r, 0, Math.PI * 2);
    }
    ctx.stroke();
  }
}

// The community (at the map's current resolution) holding most matching papers.
function topCommunity(L, counts) {
  const resolution = currentResolution() ?? L.legend?.default_resolution;
  if (resolution == null) return null;
  const tally = new Map();
  for (let i = 0; i < L.n; i++) {
    if (!counts[i]) continue;
    const cid = L.communities[i][resolution];
    if (cid == null || cid < 0) continue;
    tally.set(cid, (tally.get(cid) || 0) + 1);
  }
  let best = null, bestN = 0;
  for (const [cid, c] of tally) if (c > bestN) { best = cid; bestN = c; }
  if (best === null) return null;
  const name = L.legend?.by_resolution?.[resolution]?.[String(best)]?.name || `community ${best}`;
  return { name, count: bestN, resolution };
}

// ── Grid ──────────────────────────────────────────────────────────────────
function buildGrid() {
  const grid = el("wm-grid");
  grid.style.gridTemplateColumns = `110px repeat(${FIELDS.length}, minmax(0, 1fr))`;
  grid.innerHTML = '<div class="wm-corner"></div>' +
    FIELDS.map((f) => `<div class="wm-colhead">${f.label}<div class="wm-n" id="wm-n-${f.key}"></div></div>`).join("");
  for (const L of state.layouts) {
    grid.insertAdjacentHTML("beforeend",
      `<div class="wm-rowhead"><div class="wm-lab">${L.label}</div><div class="wm-note" id="wm-note-${L.key}"></div></div>`);
    for (const f of FIELDS) {
      grid.insertAdjacentHTML("beforeend", `
        <div class="wm-cell">
          <div class="wm-cell-title">${L.label} · ${f.label}</div>
          <canvas id="wm-c-${L.key}-${f.key}"></canvas>
          <div class="wm-foot">
            <span class="wm-top" id="wm-top-${L.key}-${f.key}"></span>
            <button class="wm-save" type="button" data-layout="${L.key}" data-field="${f.key}">PNG</button>
          </div>
        </div>`);
    }
  }
  grid.querySelectorAll(".wm-save").forEach((b) => {
    b.addEventListener("click", () => savePng(b.dataset.layout, b.dataset.field, b));
  });
}

// Exports are re-rendered on a finer raster and a wider frame (so a blob at the
// edge keeps its whole gradient), with a transparent background.
function savePng(layoutKey, fieldKey, button) {
  const L = state.layouts.find((l) => l.key === layoutKey);
  const last = state.last?.[layoutKey]?.[fieldKey];
  if (!L || !last) return;
  const label = button.textContent;
  button.textContent = "…";
  setTimeout(() => {
    const bw = bandwidth(L);
    const span = Math.max(L.extent[1] - L.extent[0], L.extent[3] - L.extent[2]);
    const m = Math.max(EXPORT_MARGIN * span, 3 * bw);
    const [x0, x1, y0, y1] = L.extent;
    const extent = [x0 - m, x1 + m, y0 - m, y1 + m];
    const gx = EXPORT_GRID;
    const gy = Math.max(1, Math.round(gx * (extent[3] - extent[2]) / (extent[1] - extent[0])));
    const grid = { gx, gy, cell: (extent[1] - extent[0]) / gx };
    const f = density(L, last.weights, grid, extent, el("wm-mode").value, bw / grid.cell);
    const canvas = document.createElement("canvas");
    paint(canvas, L, f, last.counts, grid, extent, EXPORT_W, true);
    const words = parseTerms(el("wm-words").value).join("_").replace(/\W+/g, "") || "map";
    const a = document.createElement("a");
    a.download = `wordmap_${layoutKey}_${fieldKey}_${words}_${EXPORT_W}px.png`;
    a.href = canvas.toDataURL("image/png");
    a.click();
    button.textContent = label;
  }, 0);
}

// ── Recompute ─────────────────────────────────────────────────────────────
let timer = null;
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(recompute, 220);
}

function clearAll() {
  for (const L of state.layouts) for (const f of FIELDS) {
    const c = el(`wm-c-${L.key}-${f.key}`);
    c.getContext("2d").clearRect(0, 0, c.width, c.height);
    el(`wm-top-${L.key}-${f.key}`).textContent = "";
  }
  for (const f of FIELDS) el(`wm-n-${f.key}`).textContent = "";
}

async function recompute() {
  if (!state.texts) return;
  const token = ++state.token;
  const status = el("wm-status");
  let matcher;
  try {
    matcher = buildMatcher(parseTerms(el("wm-words").value), el("wm-match").value);
    status.className = "";
  } catch (err) {
    status.className = "wm-err";
    status.textContent = `Invalid regular expression: ${err.message}`;
    return;
  }
  el("wm-forms").innerHTML = matcher
    ? `searching <code>${matcher.forms.map((f) => f.replace(/[<&>]/g, "")).join(" | ")}</code>`
    : "";
  if (!matcher) {
    status.textContent = "Type a word to search.";
    clearAll();
    return;
  }
  status.textContent = "Computing…";
  await new Promise((r) => setTimeout(r, 0));
  if (token !== state.token) return;

  const byId = countById(matcher);
  const how = el("wm-weight").value;
  const mode = el("wm-mode").value;
  state.last = {};

  // Column headers count papers in the largest view (the citation layout).
  const reference = state.layouts[0];
  for (const f of FIELDS) {
    const { counts } = weightsFor(reference, byId, f.key, "presence");
    let papers = 0, mentions = 0;
    for (let i = 0; i < counts.length; i++) if (counts[i]) { papers++; mentions += counts[i]; }
    el(`wm-n-${f.key}`).textContent = `${papers.toLocaleString()} papers · ${mentions.toLocaleString()} mentions`;
  }

  const dpr = window.devicePixelRatio || 1;
  for (const L of state.layouts) {
    const kept = keptMask(L);
    let shown = L.n;
    if (kept) { shown = 0; for (let i = 0; i < L.n; i++) shown += kept[i]; }
    el(`wm-note-${L.key}`).textContent = kept
      ? `${shown.toLocaleString()} of ${L.n.toLocaleString()} papers (well connected at ${currentResolution()})`
      : `${L.n.toLocaleString()} papers`;
    state.last[L.key] = {};
    for (const f of FIELDS) {
      if (token !== state.token) return;
      const last = weightsFor(L, byId, f.key, how);
      state.last[L.key][f.key] = last;
      const canvas = el(`wm-c-${L.key}-${f.key}`);
      const fld = density(L, last.weights, L.grid, L.extent, mode, bandwidth(L) / L.grid.cell);
      paint(canvas, L, fld, last.counts, L.grid, L.extent, (canvas.clientWidth || 300) * dpr, false);
      canvas.style.height = Math.round(canvas.height / dpr) + "px";
      const top = topCommunity(L, last.counts);
      el(`wm-top-${L.key}-${f.key}`).textContent = top
        ? `most in ${top.name} (${top.count}) at ${top.resolution}` : "no matches";
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  if (token === state.token) status.textContent = "";
}

// ── Wiring ────────────────────────────────────────────────────────────────
async function start() {
  if (state.started) return;
  state.started = true;
  const match = el("wm-match");
  match.innerHTML = MATCH_MODES.map((m) => `<option value="${m.value}">${m.label}</option>`).join("");
  try {
    await load();
  } catch (err) {
    const status = el("wm-status");
    status.className = "wm-err";
    status.textContent = `Could not load the map data: ${err.message}`;
    return;
  }
  buildGrid();
  const resolutions = Object.keys(state.layouts[0]?.legend?.by_resolution || {})
    .sort((a, b) => parseFloat(a) - parseFloat(b));
  const sel = el("wm-resolution");
  sel.innerHTML = resolutions.map((r) => `<option value="${r}">${r}</option>`).join("");
  syncFromMap();
  sel.addEventListener("change", () => {
    window.scientographer?.setCommunityResolution?.(sel.value);
    recompute();
  });
  el("wm-well-connected").addEventListener("change", () => {
    window.scientographer?.setWellConnectedOnly?.(el("wm-well-connected").checked);
    recompute();
  });
  el("wm-words").addEventListener("input", schedule);
  for (const id of ["wm-match", "wm-weight", "wm-mode", "wm-dots", "wm-rings"]) {
    el(id).addEventListener("change", recompute);
  }
  el("wm-bw").addEventListener("input", () => {
    el("wm-bw-val").textContent = (Number(el("wm-bw").value) / 10).toFixed(1) + "%";
    schedule();
  });
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (!el("view-wordmap")?.classList.contains("active")) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(recompute, 250);
  });
  recompute();
}

// Take the map's current resolution and filter setting.
function syncFromMap() {
  const map = window.scientographer;
  const resolution = map?.communityResolution?.();
  const sel = el("wm-resolution");
  if (sel && resolution != null && [...sel.options].some((o) => o.value === String(resolution))) {
    sel.value = String(resolution);
  }
  const box = el("wm-well-connected");
  if (box && map?.wellConnectedOnly) box.checked = map.wellConnectedOnly();
  const anyMasks = state.layouts.some((L) => L.wellConnected);
  if (box) box.closest(".wm-check").hidden = !anyMasks;
}

// Loads lazily, the first time the tab is opened (the abstracts are large);
// afterwards it picks up any change made on the map meanwhile.
document.getElementById("tab-wordmap")?.addEventListener("click", () => {
  if (state.started) {
    setTimeout(() => { syncFromMap(); recompute(); }, 0);
  } else {
    setTimeout(start, 0);
  }
});
