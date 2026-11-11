// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// The map's density layer: a heatmap (kernel density estimate, kde.js) of the
// papers the filters select, drawn on the 2D map under or over the dots. It
// shows their density (where they are), or their enrichment against every
// paper on the map (where they are over-represented: their share of the
// density over the map's own, 1x = the same share as anywhere).
//
// The points are exactly the papers counted in "Nodes selected" (text filters,
// years, well-connected papers, muted groups, time snapshot), so the layer
// follows every filter. It stays blank until a filter narrows the selection.
//
// The density is computed once per selection in the layout's own coordinates
// (before the map's rotation and flips), normalised to its maximum, coloured,
// and redrawn at every frame through the same transform as the dots: panning,
// zooming and turning the map never recompute it, and its colours stay put.
// The bandwidth is a share of the view's extent (robustSpan over all its
// papers), so it is the same whatever the filters select.

import { PALETTES, cssGradient, densityGrid, enrichmentGrid, gridCanvas, gridFrame, lut, robustSpan } from "./kde.js?v=2";

const RING_COLOR = { dark: "rgba(255, 240, 200, 0.9)", light: "rgba(103, 0, 13, 0.9)" };

export function createDensityLayer(map) {
  // map: { container, renderer(), is3D(), points() -> { xs, ys, nodes } | null
  //        (layout coordinates of the selected papers, or null when no filter
  //        narrows the selection; points(weight) also returns their weights,
  //        and weighted = false when there is no text to count), basePositions() -> { xs, ys } (every paper
  //        of the view), toGraph(x, y) -> [x, y] (the map's rotation and flips),
  //        viewKey(), filtersActive() (false while the Filters switch is off or
  //        Space is held: the layer then hides, keeping its density),
  //        refresh() (redraw the map's dots), background() -> { xs, ys } (every
  //        paper the view shows with the filters off: enrichment's reference) }
  const el = (id) => document.getElementById(id);
  const ui = {
    on: el("density-on"),
    controls: el("density-controls"),
    status: el("density-status"),
    weight: el("density-weight"),
    colour: el("density-colour"),
    barLow: el("density-bar-low"),
    barHigh: el("density-bar-high"),
    bandwidth: el("density-bandwidth"),
    bandwidthValue: el("density-bandwidth-value"),
    opacity: el("density-opacity"),
    opacityValue: el("density-opacity-value"),
    over: el("density-over"),
    rings: el("density-rings"),
    all: el("density-all"),
    bar: el("density-bar"),
  };
  const canvas = document.createElement("canvas");
  canvas.className = "density-layer";
  const layer = { grid: null, image: null, points: null, spans: new Map(), theme: null };

  const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
  const enabled = () => ui.on.checked && !map.is3D();

  function span() {
    const key = map.viewKey();
    if (!layer.spans.has(key)) {
      const { xs, ys } = map.basePositions();
      layer.spans.set(key, robustSpan(xs, ys));
    }
    return layer.spans.get(key);
  }
  const share = () => Number(ui.bandwidth.value) / 100;
  const sigma = () => share() * span();

  function place() {
    const ref = map.container.querySelector(ui.over.checked ? ".sigma-labels" : ".sigma-nodes");
    if (ref && canvas.nextSibling !== ref) map.container.insertBefore(canvas, ref);
  }

  function setStatus(text) {
    ui.status.textContent = text;
  }

  // Recompute the density for the current selection, then redraw: the dots too
  // when the left-out papers are shown under the heat, since which of them are
  // faded depends on the new density.
  function update() {
    compute();
    if (ui.all.checked) map.refresh();
    else draw();
  }

  function compute() {
    ui.controls.hidden = !ui.on.checked;
    if (ui.on.checked && map.is3D()) {
      setStatus("The density layer works on the 2D views for now; switch the view to one of them.");
    }
    layer.grid = layer.image = layer.points = null;
    if (!enabled()) return;
    const points = map.points(ui.weight.value);
    if (!points) {
      setStatus("Filter the papers (text, years) to see where they sit.");
      return;
    }
    if (!points.xs.length) {
      setStatus("No papers match the filters.");
      return;
    }
    const t0 = performance.now();
    const s = sigma();
    const enrichment = ui.colour.value === "enrichment";
    let background = null;
    if (enrichment) {
      // Both densities on one frame, wide enough (6 sigma) that the papers
      // around the selection's edge count in the background too.
      background = map.background();
      const frame = gridFrame(points.xs, points.ys, s, 6);
      layer.grid = enrichmentGrid(
        densityGrid(points.xs, points.ys, s, points.weights, frame),
        densityGrid(background.xs, background.ys, s, null, frame));
    } else {
      layer.grid = densityGrid(points.xs, points.ys, s, points.weights);
    }
    layer.theme = theme();
    layer.image = gridCanvas(layer.grid, lut(PALETTES[layer.theme]));
    layer.points = points;
    const ms = performance.now() - t0;
    ui.barLow.textContent = enrichment ? "1×" : "Low";
    ui.barHigh.textContent = enrichment ? `${fmtRatio(layer.grid.max)}×` : "High";
    const weighting = ui.weight.value === "presence" ? ""
      : points.weighted ? `, weighted by ${ui.weight.selectedOptions[0].textContent.toLowerCase()}`
      : " (weights count the words of a text filter; with none, each paper counts once)";
    const what = enrichment
      ? `Enrichment of ${points.xs.length.toLocaleString()} papers${weighting} against the ` +
        `${background.xs.length.toLocaleString()} on the map: up to ${fmtRatio(layer.grid.max)}× their share ` +
        `of the map (1× = the same share as anywhere; log scale; where the map has few papers it fades out).`
      : `Density of ${points.xs.length.toLocaleString()} papers${weighting}, normalised to its maximum.`;
    setStatus(`${what} Bandwidth ${+(share() * 100).toFixed(2)}% of the map (σ = ${fmt(s)} layout units; ` +
      `${layer.grid.gx}×${layer.grid.gy} cells, ${ms.toFixed(0)} ms).`);
  }

  // Redraw the coloured grid where the dots are now (every frame).
  function draw() {
    const renderer = map.renderer();
    const show = enabled() && layer.image && renderer && !map.is3D() && map.filtersActive();
    canvas.hidden = !show;
    if (!show) return;
    place();
    const dpr = window.devicePixelRatio || 1;
    const { width, height } = renderer.getDimensions();
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Image pixel (u, v) is centred on grid node (u, gy - 1 - v); map three
    // corners of the image to the screen and draw it through that affine map.
    const { x0, y0, cell, gx, gy } = layer.grid;
    const screen = (u, v) => {
      const [x, y] = map.toGraph(x0 + (u - 0.5) * cell, y0 + (gy - 0.5 - v) * cell);
      return renderer.graphToViewport({ x, y });
    };
    const o = screen(0, 0), ex = screen(gx, 0), ey = screen(0, gy);
    ctx.setTransform(
      dpr * (ex.x - o.x) / gx, dpr * (ex.y - o.y) / gx,
      dpr * (ey.x - o.x) / gy, dpr * (ey.y - o.y) / gy,
      dpr * o.x, dpr * o.y);
    ctx.globalAlpha = Number(ui.opacity.value);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(layer.image, 0, 0);
    ctx.globalAlpha = 1;

    if (ui.rings.checked && layer.points) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.strokeStyle = RING_COLOR[layer.theme];
      ctx.lineWidth = 1;
      ctx.beginPath();
      const { xs, ys } = layer.points;
      for (let i = 0; i < xs.length; i++) {
        const [x, y] = map.toGraph(xs[i], ys[i]);
        const p = renderer.graphToViewport({ x, y });
        if (p.x < -5 || p.y < -5 || p.x > width + 5 || p.y > height + 5) continue;
        ctx.moveTo(p.x + 3.5, p.y);
        ctx.arc(p.x, p.y, 3.5, 0, 2 * Math.PI);
      }
      ctx.stroke();
    }
  }

  // Controls.
  ui.bar.style.background = cssGradient(PALETTES[theme()]);
  const showValues = () => {
    ui.bandwidthValue.textContent = `${+Number(ui.bandwidth.value).toFixed(2)}%`;
    ui.opacityValue.textContent = `${+(Number(ui.opacity.value) * 100).toFixed(1)}%`;
  };
  showValues();
  let timer = null;
  ui.on.addEventListener("change", update);
  ui.weight.addEventListener("change", update);
  ui.colour.addEventListener("change", update);
  ui.bandwidth.addEventListener("input", () => {
    showValues();
    clearTimeout(timer);
    timer = setTimeout(update, 120);
  });
  ui.opacity.addEventListener("input", () => { showValues(); draw(); });
  ui.over.addEventListener("change", draw);
  ui.rings.addEventListener("change", draw);
  ui.all.addEventListener("change", map.refresh);

  return {
    update,
    draw,
    // The left-out papers are drawn faded while the layer shows a density.
    showsAll: () => ui.all.checked && enabled() && !!layer.image && map.filtersActive(),
    attach(renderer) { renderer.on("afterRender", draw); },
    forgetView() { layer.spans.clear(); },
  };
}

function fmtRatio(v) {
  return v >= 10 ? v.toFixed(0) : v.toFixed(1);
}

function fmt(v) {
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(1) : v.toPrecision(2);
}

