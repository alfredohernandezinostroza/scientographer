// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// A colour picker drawn in the page, the same in every browser (the native
// <input type="color"> hands over to the operating system's dialog in some of
// them, which the page cannot extend).
//
// A saturation/brightness square, a hue bar, a hex box, quick colours (the
// grouping's palette) and the viewer's own colours: MY_COLOUR_SLOTS slots kept
// in this browser; "Save" asks which slot to fill or replace.
//
// While it is open, the pointer outside it is an eyedropper: hovering a dot on
// the map previews its colour (through `sampler`, which the map drives) and a
// click takes it; a click on a legend swatch, a map label or a group tag takes
// that colour, and does nothing else (no muting, no opening). "Pick from
// screen" takes any pixel, where the browser has an eyedropper (Chrome, Edge).
//
// Changes show live through onInput; Done keeps the colour (onDone), Cancel or
// Escape goes back to the starting one (onInput with it, then onCancel).

const MY_COLOURS_KEY = "scientographer-my-colours";
const MY_COLOUR_SLOTS = 16;
const SAMPLE_SELECTOR = ".swatch, .cluster-label, .cluster-tag, .colour-slot.filled, .colour-quick";

let open = null; // the picker on screen, if any

// ── Colour maths ────────────────────────────────────────────────────────────
function parseHex(text) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((text || "").trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}
function cssToRgb(css) {
  const hex = parseHex(css);
  if (hex) return hex;
  const m = /rgba?\(([^)]+)\)/i.exec(css || "");
  return m ? m[1].split(",").slice(0, 3).map((v) => Math.round(parseFloat(v))) : null;
}
const toHex = (rgb) => "#" + rgb.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
function rgbToHsv([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [(h * 60 + 360) % 360, max ? d / max : 0, max];
}
function hsvToRgb([h, s, v]) {
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return 255 * (v - v * s * Math.max(0, Math.min(k, 4 - k, 1)));
  };
  return [f(5), f(3), f(1)];
}

// ── The viewer's own colours ────────────────────────────────────────────────
function loadMyColours() {
  try {
    const saved = JSON.parse(localStorage.getItem(MY_COLOURS_KEY) || "null");
    if (Array.isArray(saved)) return Array.from({ length: MY_COLOUR_SLOTS }, (_, i) => saved[i] || null);
  } catch {
    // storage blocked: empty slots
  }
  return new Array(MY_COLOUR_SLOTS).fill(null);
}
function saveMyColours(slots) {
  try {
    localStorage.setItem(MY_COLOURS_KEY, JSON.stringify(slots));
  } catch {
    // not kept (private window): the slots still work until the page closes
  }
}

// ── The picker ──────────────────────────────────────────────────────────────
export function closeColourPicker() {
  open?.cancel();
}

export function openColourPicker({ anchor, color, title = "", quick = [], onInput, onDone, onCancel, sampler }) {
  closeColourPicker();
  const start = toHex(cssToRgb(color) || [136, 136, 136]);
  let hsv = rgbToHsv(cssToRgb(start));
  let current = start;
  let choosingSlot = false;
  const slots = loadMyColours();

  const box = document.createElement("div");
  box.className = "colour-picker";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-label", `Colour${title ? " of " + title : ""}`);
  box.innerHTML = `
    <div class="cp-title"></div>
    <div class="cp-sv"><div class="cp-sv-white"></div><div class="cp-sv-black"></div><div class="cp-sv-handle"></div></div>
    <div class="cp-hue"><div class="cp-hue-handle"></div></div>
    <div class="cp-row">
      <div class="cp-compare" title="Before · now"><span class="cp-before"></span><span class="cp-now"></span></div>
      <input class="cp-hex" type="text" spellcheck="false" maxlength="7" aria-label="Hex colour">
      <button class="cp-screen" type="button" title="Take the colour of any pixel on the screen" hidden>Pick from screen</button>
    </div>
    <div class="cp-label">Palette</div>
    <div class="cp-quick"></div>
    <div class="cp-label">My colours <button class="cp-save" type="button" title="Save the current colour in a slot of your choice">Save…</button></div>
    <div class="cp-slots"></div>
    <div class="cp-hint">Outside this box the pointer is an eyedropper: hover a dot to preview its colour, click a dot, a legend colour or a label to take it.</div>
    <div class="cp-actions"><button class="cp-cancel" type="button">Cancel</button><button class="cp-done primary" type="button">Done</button></div>`;
  box.querySelector(".cp-title").textContent = title;
  const $ = (sel) => box.querySelector(sel);
  const sv = $(".cp-sv"), svHandle = $(".cp-sv-handle"), hue = $(".cp-hue"), hueHandle = $(".cp-hue-handle");
  const hex = $(".cp-hex"), now = $(".cp-now"), hint = $(".cp-hint");
  $(".cp-before").style.background = start;
  $(".cp-before").addEventListener("click", () => setColour(start));

  for (const c of quick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "colour-quick";
    b.style.background = c;
    b.title = c;
    b.addEventListener("click", () => setColour(c));
    $(".cp-quick").appendChild(b);
  }
  if (!quick.length) { $(".cp-quick").remove(); box.querySelectorAll(".cp-label")[0].remove(); }

  function drawSlots() {
    const holder = $(".cp-slots");
    holder.innerHTML = "";
    holder.classList.toggle("choosing", choosingSlot);
    slots.forEach((c, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "colour-slot" + (c ? " filled" : "");
      if (c) b.style.background = c;
      b.title = choosingSlot ? `Save ${current} here${c ? ` (replaces ${c})` : ""}` : c || "Empty";
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        if (choosingSlot) {
          slots[i] = current;
          saveMyColours(slots);
          choosingSlot = false;
          hint.textContent = `Saved ${current} in slot ${i + 1}.`;
          drawSlots();
        } else if (c) {
          setColour(c);
        }
      });
      holder.appendChild(b);
    });
  }
  $(".cp-save").addEventListener("click", () => {
    choosingSlot = !choosingSlot;
    hint.textContent = choosingSlot ? `Click a slot to save ${current} there (a filled one is replaced); Escape cancels.` : "";
    drawSlots();
  });
  drawSlots();

  // Show the colour; tell the map (at most once a frame).
  let pending = false;
  function render() {
    current = toHex(hsvToRgb(hsv));
    sv.style.background = `hsl(${hsv[0]}, 100%, 50%)`;
    svHandle.style.left = `${hsv[1] * 100}%`;
    svHandle.style.top = `${(1 - hsv[2]) * 100}%`;
    hueHandle.style.left = `${(hsv[0] / 360) * 100}%`;
    now.style.background = current;
    if (document.activeElement !== hex) hex.value = current;
    if (!pending) {
      pending = true;
      requestAnimationFrame(() => { pending = false; onInput?.(current); });
    }
  }
  function setColour(css) {
    const rgb = cssToRgb(css);
    if (!rgb) return;
    hsv = rgbToHsv(rgb);
    hex.value = toHex(rgb);
    render();
  }

  function drag(el, onMove) {
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const r = el.getBoundingClientRect();
        onMove(Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)));
        render();
      };
      move(e);
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", () => el.removeEventListener("pointermove", move), { once: true });
    });
  }
  drag(sv, (x, y) => { hsv = [hsv[0], x, 1 - y]; });
  drag(hue, (x) => { hsv = [Math.min(359.9, x * 360), hsv[1], hsv[2]]; });
  hex.addEventListener("input", () => { if (parseHex(hex.value)) setColour(hex.value); });
  hex.addEventListener("blur", () => { hex.value = current; });

  if ("EyeDropper" in window) {
    const button = $(".cp-screen");
    button.hidden = false;
    button.addEventListener("click", async () => {
      try {
        const { sRGBHex } = await new window.EyeDropper().open();
        setColour(sRGBHex);
      } catch {
        // cancelled
      }
    });
  }

  // Eyedropper outside the box: the map reports dots, the page the rest.
  const preview = (css) => { const rgb = cssToRgb(css); if (rgb) now.style.background = toHex(rgb); };
  sampler?.start({ hover: preview, leave: () => { now.style.background = current; }, take: setColour });
  const onClick = (e) => {
    if (box.contains(e.target)) return;
    if (e.target.closest?.("#sigma-container, #three-container")) return; // dots: the sampler takes them
    e.preventDefault();
    e.stopPropagation();
    const el = e.target.closest?.(SAMPLE_SELECTOR);
    const swatch = el?.matches(".cluster-label") ? el.querySelector(".swatch") : el;
    if (swatch) setColour(getComputedStyle(swatch).backgroundColor);
  };
  const onOver = (e) => {
    if (box.contains(e.target)) return;
    const el = e.target.closest?.(SAMPLE_SELECTOR);
    const swatch = el?.matches(".cluster-label") ? el.querySelector(".swatch") : el;
    if (swatch) preview(getComputedStyle(swatch).backgroundColor);
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      if (choosingSlot) { choosingSlot = false; hint.textContent = ""; drawSlots(); } else cancel();
    } else if (e.key === "Enter" && e.target === hex) {
      done();
    }
  };
  document.addEventListener("click", onClick, true);
  document.addEventListener("mouseover", onOver, true);
  document.addEventListener("keydown", onKey, true);
  box.addEventListener("pointerenter", () => document.body.classList.remove("colour-sampling"));
  box.addEventListener("pointerleave", () => document.body.classList.add("colour-sampling"));

  function close() {
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("mouseover", onOver, true);
    document.removeEventListener("keydown", onKey, true);
    document.body.classList.remove("colour-sampling");
    sampler?.stop();
    box.remove();
    open = null;
  }
  function done() { close(); onDone?.(current); }
  function cancel() { close(); onInput?.(start); onCancel?.(); }
  $(".cp-done").addEventListener("click", done);
  $(".cp-cancel").addEventListener("click", cancel);
  open = { cancel };

  // Beside the anchor's panel (so the legend and the map stay in sight), inside
  // the window.
  document.body.appendChild(box);
  const a = anchor.getBoundingClientRect(), b = box.getBoundingClientRect();
  const panel = anchor.closest(".panel, #controls")?.getBoundingClientRect() || a;
  let left = panel.right + 10, top = a.top - 20;
  if (left + b.width > window.innerWidth - 8) left = Math.max(8, a.left - b.width - 10);
  top = Math.min(Math.max(8, top), window.innerHeight - b.height - 8);
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  setColour(start);
  hex.focus();
  hex.select();
}
