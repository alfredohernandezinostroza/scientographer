// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// 3D map renderer (three.js), loaded only when a 3D view is opened.
//
// It draws the same graphology graph as the 2D sigma renderer, through the same
// nodeReducer / edgeReducer, and answers the part of sigma's API main.js uses
// (refresh, on(event), getCamera, getDimensions, graphToViewport,
// getNodeDisplayData, kill), so the legend, search, filters, selection and
// detail panel work unchanged. Nodes carry x, y and z.
//
// The papers' centroid sits at the origin: the camera orbits it and the
// orientation buttons turn the map about it, relative to the screen (turn about
// the vertical, tilt about the horizontal, rotate about the line of sight,
// mirror left-right or top-bottom). Like the 2D map, the orientation moves the
// papers, not the camera, so a camera reset keeps it.

import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.160.0/+esm";
import { OrbitControls } from "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js/+esm";

const BACKGROUND = 0x0e1116; // styles.css --bg
const STEP = Math.PI / 12; // 15° per orientation click
const CLICK_PIXELS = 5; // a press that moves less than this...
const CLICK_MS = 500; // ...and is released this fast is a click, not a drag
const SIZE_SCALE = 1; // sigma node sizes -> point sizes (the reducer applies the Dot size slider)

const colorCache = new Map();

function parseColor(color) {
  let parsed = colorCache.get(color);
  if (!parsed) {
    parsed = parseColorUncached(color);
    colorCache.set(color, parsed);
  }
  return parsed;
}

function parseColorUncached(color) {
  const rgba = /rgba?\(([^)]+)\)/i.exec(color || "");
  if (rgba) {
    const p = rgba[1].split(",").map((v) => parseFloat(v));
    return [p[0] / 255, p[1] / 255, p[2] / 255, p.length > 3 ? p[3] : 1];
  }
  const c = new THREE.Color();
  try { c.setStyle(color || "#888888"); } catch { c.set("#888888"); }
  return [c.r, c.g, c.b, 1];
}

export class Renderer3D {
  constructor(graph, container, { nodeReducer, edgeReducer }) {
    this.graph = graph;
    this.container = container;
    this.nodeReducer = nodeReducer;
    this.edgeReducer = edgeReducer;
    this.handlers = {};
    this.n = 0;
    this.radius = 1;
    this.shown = new Uint8Array(0);
    this.display = [];

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(BACKGROUND);
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1e6);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    // The camera circles the map slowly until the viewer stops it (the Auto-rotate
    // button); people who ask their system for reduced motion start without it.
    this.controls.autoRotateSpeed = 0.6;
    this.controls.autoRotate = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    this.controls.addEventListener("change", () => this._requestFrame());

    // Everything that moves with the orientation lives in `map`, whose matrix is
    // the orientation; the centroid offset sits on its children.
    this.map = new THREE.Group();
    this.map.matrixAutoUpdate = false;
    this.scene.add(this.map);
    this.orientation = new THREE.Matrix4();

    this.pointsMaterial = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      uniforms: { scale: { value: 1 } },
      vertexShader: `attribute float size; attribute vec4 rgba; varying vec4 vColor; uniform float scale;
        void main() {
          vColor = rgba;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = size * scale * (300.0 / -mv.z);
          gl_Position = size > 0.0 ? projectionMatrix * mv : vec4(2.0, 2.0, 2.0, 1.0);
        }`,
      fragmentShader: `varying vec4 vColor;
        void main() {
          float r = length(gl_PointCoord - 0.5);
          if (r > 0.5) discard;
          gl_FragColor = vec4(vColor.rgb, vColor.a * smoothstep(0.5, 0.38, r));
        }`,
    });
    this.edgeMaterial = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false });

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.hovered = null;
    this._bindPointer();

    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(container);
    this.resize();
    this._frameRequested = false;
    this.renderer.setAnimationLoop(() => this._frame());
  }

  // ── sigma-compatible API ──────────────────────────────────────────────────
  on(event, handler) {
    (this.handlers[event] ||= []).push(handler);
    return this;
  }

  _emit(event, payload) {
    for (const h of this.handlers[event] || []) h(payload);
  }

  getDimensions() {
    return { width: this.container.clientWidth, height: this.container.clientHeight };
  }

  // The 2D map's label density follows the camera ratio; here the distance to
  // the centroid relative to the reset view plays that part.
  getCamera() {
    const self = this;
    return {
      getState: () => ({ ratio: self.camera.position.distanceTo(self.controls.target) / self._homeDistance() }),
      animatedReset: () => self.resetView(),
      animate: () => self.resetView(),
    };
  }

  // Screen position (CSS pixels) of a graph-space point {x, y, z}; points behind
  // the camera come back far off-screen so labels there are culled.
  graphToViewport({ x, y, z = 0 }) {
    const p = new THREE.Vector3(x, y, z).applyMatrix4(this.points ? this.points.matrixWorld : new THREE.Matrix4());
    p.project(this.camera);
    if (p.z > 1) return { x: -1e5, y: -1e5 };
    const { width, height } = this.getDimensions();
    return { x: (p.x + 1) / 2 * width, y: (1 - p.y) / 2 * height };
  }

  getNodeDisplayData(node) {
    const i = parseInt(node, 10);
    return this.display[i] && !this.display[i].hidden ? this.display[i] : null;
  }

  scaleSize(size) {
    return size;
  }

  setAutoRotate(on) {
    this.controls.autoRotate = on;
    this._requestFrame();
  }

  get autoRotate() {
    return this.controls.autoRotate;
  }

  setVisible(visible) {
    this.container.hidden = !visible;
    this.active = visible;
    if (visible) {
      this.resize();
      this._requestFrame();
    }
  }

  kill() {
    this.renderer.setAnimationLoop(null);
    this._resizeObserver.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  resize() {
    const { width, height } = this.getDimensions();
    if (!width || !height) return;
    this.renderer.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this._requestFrame();
  }

  // Rebuild positions (when the graph changed), then colours, sizes and edges
  // from the reducers: the same rules as the 2D map.
  refresh() {
    if (this.graph.order !== this.n || this._layoutStale) this._rebuild();
    const size = this.sizeAttr.array, rgba = this.rgbaAttr.array;
    this.graph.forEachNode((node, attrs) => {
      const i = parseInt(node, 10);
      const a = this.nodeReducer(node, attrs);
      const hidden = !!a.hidden;
      this.shown[i] = hidden ? 0 : 1;
      size[i] = hidden ? 0 : (a.size || 1) * SIZE_SCALE;
      rgba.set(parseColor(a.color), i * 4);
      this.display[i] = { x: attrs.x, y: attrs.y, z: attrs.z || 0, size: a.size, color: a.color, hidden };
    });
    this.sizeAttr.needsUpdate = true;
    this.rgbaAttr.needsUpdate = true;
    this._refreshEdges();
    this._requestFrame();
  }

  // ── 3D-only ───────────────────────────────────────────────────────────────
  // Straight at the front, x to the right and y up: the extra layouts are lined up
  // with the 2D map, so this first view shows the communities where the map has them.
  resetView() {
    this.camera.position.set(0, 0, this._homeDistance());
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    this._requestFrame();
  }

  frameNode(idx) {
    const d = this.display[idx];
    if (!d) return;
    const p = new THREE.Vector3(d.x, d.y, d.z).applyMatrix4(this.points.matrixWorld);
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    this.controls.target.copy(p);
    this.camera.position.copy(p).addScaledVector(direction, this.radius * 0.35);
    this.controls.update();
    this._requestFrame();
  }

  // action: turn-left/right (screen vertical), tilt-up/down (screen horizontal),
  // rotate-left/right (line of sight), flip-horizontal/vertical, reset.
  orient(action) {
    const axis = (k) => new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, k).normalize();
    const turn = (k, angle) => new THREE.Matrix4().makeRotationAxis(axis(k), angle);
    const mirror = (k) => {
      const a = axis(k);
      return new THREE.Matrix4().set(
        1 - 2 * a.x * a.x, -2 * a.x * a.y, -2 * a.x * a.z, 0,
        -2 * a.y * a.x, 1 - 2 * a.y * a.y, -2 * a.y * a.z, 0,
        -2 * a.z * a.x, -2 * a.z * a.y, 1 - 2 * a.z * a.z, 0,
        0, 0, 0, 1);
    };
    const change = {
      "turn-left": () => turn(1, -STEP),
      "turn-right": () => turn(1, STEP),
      "tilt-up": () => turn(0, -STEP),
      "tilt-down": () => turn(0, STEP),
      "rotate-left": () => turn(2, STEP),
      "rotate-right": () => turn(2, -STEP),
      "flip-horizontal": () => mirror(0),
      "flip-vertical": () => mirror(1),
    }[action];
    if (action === "orientation-reset") this.orientation.identity();
    else if (change) this.orientation.premultiply(change());
    else return;
    this.map.matrix.copy(this.orientation);
    this.map.matrixWorldNeedsUpdate = true;
    this._requestFrame();
  }

  // ── internals ─────────────────────────────────────────────────────────────
  _homeDistance() {
    return this.radius * 2.2;
  }

  _rebuild() {
    this._layoutStale = false;
    this.n = this.graph.order;
    const positions = new Float32Array(this.n * 3);
    const centroid = new THREE.Vector3();
    this.graph.forEachNode((node, attrs) => {
      const i = parseInt(node, 10);
      positions.set([attrs.x, attrs.y, attrs.z || 0], i * 3);
      centroid.x += attrs.x; centroid.y += attrs.y; centroid.z += attrs.z || 0;
    });
    if (this.n) centroid.divideScalar(this.n);
    this.positions = positions;
    this.shown = new Uint8Array(this.n);
    this.display = new Array(this.n);

    if (this.points) {
      this.map.remove(this.points);
      this.points.geometry.dispose();
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    this.sizeAttr = new THREE.BufferAttribute(new Float32Array(this.n), 1);
    this.rgbaAttr = new THREE.BufferAttribute(new Float32Array(this.n * 4), 4);
    geometry.setAttribute("size", this.sizeAttr);
    geometry.setAttribute("rgba", this.rgbaAttr);
    this.points = new THREE.Points(geometry, this.pointsMaterial);
    this.points.position.copy(centroid).negate();
    this.map.add(this.points);

    // The extent: the distance from the centroid within which 99% of papers lie
    // (a few detached papers far out would otherwise shrink everything).
    const distances = [];
    for (let i = 0; i < this.n; i++) {
      distances.push(Math.hypot(positions[i * 3] - centroid.x, positions[i * 3 + 1] - centroid.y,
        positions[i * 3 + 2] - centroid.z));
    }
    distances.sort((a, b) => a - b);
    this.radius = distances.length ? distances[Math.floor(0.99 * (distances.length - 1))] || 1 : 1;
    this.pointsMaterial.uniforms.scale.value = this.radius / 120;
    this.camera.near = this.radius / 1000;
    this.camera.far = this.radius * 50;
    this.camera.updateProjectionMatrix();
    this.resetView();
  }

  // Positions changed in place (e.g. a new view): rebuild on the next refresh.
  invalidateLayout() {
    this._layoutStale = true;
  }

  _refreshEdges() {
    const segments = [], colors = [];
    const p = this.positions;
    this.graph.forEachEdge((edge, attrs, source, target) => {
      const a = this.edgeReducer(edge, attrs);
      if (a.hidden) return;
      const s = parseInt(source, 10), t = parseInt(target, 10);
      if (!this.shown[s] || !this.shown[t]) return;
      segments.push(p[s * 3], p[s * 3 + 1], p[s * 3 + 2], p[t * 3], p[t * 3 + 1], p[t * 3 + 2]);
      const c = parseColor(a.color || "rgba(90,102,122,0.3)");
      colors.push(...c, ...c);
    });
    if (this.lines) {
      this.map.remove(this.lines);
      this.lines.geometry.dispose();
      this.lines = null;
    }
    if (!segments.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(segments), 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colors), 4));
    this.lines = new THREE.LineSegments(geometry, this.edgeMaterial);
    this.lines.position.copy(this.points.position);
    this.map.add(this.lines);
  }

  _pick(clientX, clientY) {
    if (!this.points) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.params.Points.threshold = this.radius / 250;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObject(this.points).find((h) => this.shown[h.index]);
    return hit ? String(hit.index) : null;
  }

  _bindPointer() {
    const el = this.renderer.domElement;
    let press = null;
    let pending = null;
    el.addEventListener("pointermove", (e) => { pending = e; this._requestFrame(); });
    el.addEventListener("pointerleave", () => {
      pending = null;
      if (this.hovered !== null) {
        this.hovered = null;
        this._emit("leaveNode", {});
      }
    });
    el.addEventListener("pointerdown", (e) => {
      press = e.button === 0 ? { x: e.clientX, y: e.clientY, t: performance.now() } : null;
    });
    el.addEventListener("pointerup", (e) => {
      if (!press) return;
      const click = Math.hypot(e.clientX - press.x, e.clientY - press.y) < CLICK_PIXELS &&
        performance.now() - press.t < CLICK_MS;
      press = null;
      if (!click) return;
      const node = this._pick(e.clientX, e.clientY);
      if (node !== null) this._emit("clickNode", { node });
      else this._emit("clickStage", {});
    });
    // Hover is resolved once per frame, from the latest pointer position.
    this._hover = () => {
      if (!pending) return;
      const e = pending;
      pending = null;
      const node = this._pick(e.clientX, e.clientY);
      if (node === this.hovered) return;
      if (this.hovered !== null) this._emit("leaveNode", { node: this.hovered });
      this.hovered = node;
      if (node !== null) this._emit("enterNode", { node });
    };
  }

  _requestFrame() {
    this._frameRequested = true;
  }

  _frame() {
    if (!this.active) return;
    const moved = this.controls.update(); // damping keeps the camera moving for a moment
    if (!this._frameRequested && !moved) return;
    this._frameRequested = false;
    this._hover();
    this.renderer.render(this.scene, this.camera);
    this._emit("afterRender", {});
  }
}
