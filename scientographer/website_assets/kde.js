// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// Kernel density estimates (heatmaps) of papers on a 2D layout, shared by the
// map's density layer and the Word map tab.
//
// The density is an isotropic Gaussian KDE: each paper adds a Gaussian of
// standard deviation sigma (truncated at 3 sigma), the sum is normalised to its
// own maximum and coloured through a palette. It is computed on a grid of cells
// sigma / CELLS_PER_SIGMA wide covering the papers plus 3 sigma, so its size
// depends on how far the papers spread relative to sigma, not on the screen;
// the field is smooth at that scale, so drawing the grid with bilinear filtering
// looks the same at any zoom.

export const CELLS_PER_SIGMA = 6;
const MAX_CELLS = 2048; // per side; only reached with a tiny bandwidth

// Palettes: RGBA (0-1) at 0, 1/4, 1/2, 3/4 and 1 of the normalised density,
// linear in between. Light for white pages, dark for dark pages and slides.
export const PALETTES = {
  light: [[1, 1, 1, 0], [1, 0.85, 0.8, 0.55], [0.98, 0.35, 0.2, 0.9], [0.85, 0.05, 0.02, 1], [0.4, 0, 0, 1]],
  dark: [[0.5, 0, 0, 0], [0.75, 0.05, 0.02, 0.55], [0.95, 0.25, 0.05, 0.85], [1, 0.55, 0.1, 1], [1, 0.9, 0.55, 1]],
};

// 256 RGBA entries (0-255) through a palette's stops.
export function lut(stops) {
  const out = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    const p = (i / 255) * (stops.length - 1);
    const a = Math.min(Math.floor(p), stops.length - 2);
    const f = p - a;
    for (let c = 0; c < 4; c++) out[i * 4 + c] = 255 * (stops[a][c] + (stops[a + 1][c] - stops[a][c]) * f);
  }
  return out;
}

// CSS linear-gradient of a palette, for its colour bar.
export function cssGradient(stops) {
  const rgba = ([r, g, b, a]) => `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a})`;
  return `linear-gradient(to right, ${stops.map((s, i) => `${rgba(s)} ${(100 * i) / (stops.length - 1)}%`).join(", ")})`;
}

// The larger side of the box holding the central 99% of the points (0.5 to 99.5
// percentiles in x and in y): the scale a bandwidth is a share of. Percentiles
// keep a few far-flung papers from widening it.
export function robustSpan(xs, ys) {
  const q = (arr) => {
    const s = Float64Array.from(arr).sort();
    const at = (p) => s[Math.min(s.length - 1, Math.max(0, Math.floor(p * (s.length - 1))))];
    return at(0.995) - at(0.005);
  };
  return Math.max(q(xs), q(ys)) || 1;
}

// Separable Gaussian blur of a gx x gy grid, sigma in cells, truncated at 3 sigma.
export function blur(src, gx, gy, sigma) {
  if (sigma < 0.3) return src;
  const r = Math.max(1, Math.ceil(3 * sigma));
  const k = new Float32Array(2 * r + 1);
  let sum = 0;
  for (let i = -r; i <= r; i++) sum += (k[i + r] = Math.exp(-(i * i) / (2 * sigma * sigma)));
  for (let i = 0; i < k.length; i++) k[i] /= sum;
  const tmp = new Float32Array(gx * gy);
  for (let y = 0; y < gy; y++) {
    const row = y * gx;
    for (let x = 0; x < gx; x++) {
      let acc = 0;
      const lo = Math.max(-r, -x), hi = Math.min(r, gx - 1 - x);
      for (let i = lo; i <= hi; i++) acc += src[row + x + i] * k[i + r];
      tmp[row + x] = acc;
    }
  }
  const out = new Float32Array(gx * gy);
  for (let y = 0; y < gy; y++) {
    const lo = Math.max(-r, -y), hi = Math.min(r, gy - 1 - y);
    for (let x = 0; x < gx; x++) {
      let acc = 0;
      for (let i = lo; i <= hi; i++) acc += tmp[(y + i) * gx + x] * k[i + r];
      out[y * gx + x] = acc;
    }
  }
  return out;
}

// The density of points (xs, ys, optional weights) with bandwidth sigma (layout
// units): { x0, y0, cell, gx, gy, data (row 0 at y0, rising y), max }, or null
// with no points. Points are spread linearly over the four nearest cells
// before the blur, so the result does not jump with sub-cell positions.
export function densityGrid(xs, ys, sigma, weights = null) {
  const n = xs.length;
  if (!n || !(sigma > 0)) return null;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    if (xs[i] < x0) x0 = xs[i];
    if (xs[i] > x1) x1 = xs[i];
    if (ys[i] < y0) y0 = ys[i];
    if (ys[i] > y1) y1 = ys[i];
  }
  const pad = 3 * sigma;
  x0 -= pad; x1 += pad; y0 -= pad; y1 += pad;
  const cell = Math.max(sigma / CELLS_PER_SIGMA, Math.max(x1 - x0, y1 - y0) / MAX_CELLS);
  const gx = Math.ceil((x1 - x0) / cell) + 1;
  const gy = Math.ceil((y1 - y0) / cell) + 1;
  const h = new Float32Array(gx * gy);
  for (let i = 0; i < n; i++) {
    const w = weights ? weights[i] : 1;
    if (!w) continue;
    const fx = (xs[i] - x0) / cell, fy = (ys[i] - y0) / cell;
    const cx = Math.min(gx - 2, Math.floor(fx)), cy = Math.min(gy - 2, Math.floor(fy));
    const dx = fx - cx, dy = fy - cy;
    const o = cy * gx + cx;
    h[o] += w * (1 - dx) * (1 - dy);
    h[o + 1] += w * dx * (1 - dy);
    h[o + gx] += w * (1 - dx) * dy;
    h[o + gx + 1] += w * dx * dy;
  }
  const data = blur(h, gx, gy, sigma / cell);
  let max = 0;
  for (let i = 0; i < data.length; i++) if (data[i] > max) max = data[i];
  return { x0, y0, cell, gx, gy, data, max };
}

// The grid coloured through a palette lookup table, normalised by `max` (the
// grid's own maximum by default), as a canvas gx x gy whose top row is the
// grid's highest y.
export function gridCanvas(grid, table, max = grid.max) {
  const { gx, gy, data } = grid;
  const canvas = document.createElement("canvas");
  canvas.width = gx;
  canvas.height = gy;
  const img = new ImageData(gx, gy);
  const scale = max > 0 ? 255 / max : 0;
  for (let y = 0; y < gy; y++) {
    const src = (gy - 1 - y) * gx;
    for (let x = 0; x < gx; x++) {
      const t = Math.min(255, Math.round(data[src + x] * scale)) * 4;
      const o = (y * gx + x) * 4;
      img.data[o] = table[t];
      img.data[o + 1] = table[t + 1];
      img.data[o + 2] = table[t + 2];
      img.data[o + 3] = table[t + 3];
    }
  }
  canvas.getContext("2d").putImageData(img, 0, 0);
  return canvas;
}
