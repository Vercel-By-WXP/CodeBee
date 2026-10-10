import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const engine = readFileSync(path.resolve(here, "../ui/hive3d.js"), "utf8");

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.className = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.clientWidth = 1100;
    this.clientHeight = 620;
    this.offsetParent = {};
    this.classList = {
      add: (...tokens) => { const set = new Set(this.className.split(/\s+/).filter(Boolean)); tokens.forEach(t => set.add(t)); this.className = [...set].join(" "); },
      remove: (...tokens) => { const set = new Set(this.className.split(/\s+/).filter(Boolean)); tokens.forEach(t => set.delete(t)); this.className = [...set].join(" "); },
      toggle: (token, force) => { const set = new Set(this.className.split(/\s+/).filter(Boolean)); const on = force === undefined ? !set.has(token) : Boolean(force); if (on) set.add(token); else set.delete(token); this.className = [...set].join(" "); return on; },
      contains: token => this.className.split(/\s+/).includes(token),
    };
  }
  appendChild(child) {
    if (child.parentElement) child.parentElement.children = child.parentElement.children.filter(item => item !== child);
    this.children.push(child);
    child.parentElement = this;
    return child;
  }
  insertBefore(child, before) {
    if (child.parentElement) child.parentElement.children = child.parentElement.children.filter(item => item !== child);
    const idx = this.children.indexOf(before);
    this.children.splice(idx < 0 ? this.children.length : idx, 0, child);
    child.parentElement = this;
    return child;
  }
  replaceChildren(...items) {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
    items.forEach(item => this.appendChild(item));
  }
  querySelector(selector) {
    if (selector === ".hive-reference") return this.children.find(child => child.className === "hive-reference") || null;
    if (selector === ".hive-scene-tools") return this.children.find(child => child.className === "hive-scene-tools") || null;
    if (selector === ".hive-scene-spacer") return this.children.find(child => child.className === "hive-scene-spacer") || null;
    return null;
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== fn));
  }
  dispatch(type, extras = {}) {
    const event = { target: this, preventDefault() {}, ...extras };
    (this.listeners.get(type) || []).forEach(fn => fn(event));
  }
  closest(selector) { return selector === "button" && this.tagName === "BUTTON" ? this : null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  setPointerCapture() {}
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(item => item !== this); this.parentElement = null; }
  getContext(kind) { return kind === "webgl" || kind === "experimental-webgl" ? fakeGL : null; }
}

let drawCalls = 0;
let uploadedBytes = 0;
let nextAttrib = 0;
const fakeGL = {
  ARRAY_BUFFER: 34962, ELEMENT_ARRAY_BUFFER: 34963, STATIC_DRAW: 35044,
  FLOAT: 5126, TRIANGLES: 4, UNSIGNED_SHORT: 5123,
  VERTEX_SHADER: 35633, FRAGMENT_SHADER: 35632, COMPILE_STATUS: 35713, LINK_STATUS: 35714,
  DEPTH_TEST: 2929, CULL_FACE: 2884, BLEND: 3042, BACK: 1029,
  SRC_ALPHA: 770, ONE_MINUS_SRC_ALPHA: 771, COLOR_BUFFER_BIT: 16384, DEPTH_BUFFER_BIT: 256,
  createShader: type => ({ type, source: "" }),
  shaderSource: (shader, source) => { shader.source = source; },
  compileShader() {},
  getShaderParameter: () => true,
  getShaderInfoLog: () => "",
  createProgram: () => ({}),
  attachShader() {},
  linkProgram() {},
  getProgramParameter: () => true,
  getProgramInfoLog: () => "",
  useProgram() {},
  getAttribLocation: () => nextAttrib++,
  getUniformLocation: (_, name) => ({ name }),
  createBuffer: () => ({}),
  bindBuffer() {},
  bufferData: (_, data) => { uploadedBytes += data.byteLength || 0; },
  enable() {},
  cullFace() {},
  blendFunc() {},
  clearColor() {},
  viewport() {},
  clear() {},
  enableVertexAttribArray() {},
  vertexAttribPointer() {},
  uniformMatrix4fv() {},
  uniform3f() {},
  drawArrays: (mode, first, count) => { assert.equal(mode, 4); assert.ok(count > 0); drawCalls++; },
  depthMask() {},
  deleteBuffer() {},
  deleteProgram() {},
};

const document = {
  hidden: false,
  documentElement: { dataset: {}, attributes: {} },
  createElement: tag => new FakeElement(tag),
};
const window = { devicePixelRatio: 1, t: (key, ...args) => args.reduce((text, arg, i) => text.replace("{" + i + "}", String(arg)), key) };
window.window = window;
class FakeObserver {
  constructor(callback) { this.callback = callback; }
  observe() {}
  disconnect() {}
}
const context = {
  window, document, console, ResizeObserver: FakeObserver, MutationObserver: FakeObserver,
  setInterval: () => 1, clearInterval() {}, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  Uint16Array, Float32Array, Math, Map, Set, Error, String, Number, Boolean, Object, Array,
};
vm.runInNewContext(engine, context, { filename: "hive3d.js" });
assert.equal(typeof window.Hive3D?.create, "function", "engine exports Hive3D.create");

const viewport = new FakeElement("div");
const shell = new FakeElement("section");
const toolbar = new FakeElement("div");
toolbar.className = "hive-scene-tools";
const spacer = new FakeElement("span");
spacer.className = "hive-scene-spacer";
toolbar.appendChild(spacer);
shell.appendChild(toolbar);
shell.querySelector = selector => selector === ".hive-scene-tools" ? toolbar : null;
viewport.parentElement = shell;
const canvas = new FakeElement("canvas");
const overlay = new FakeElement("div");
overlay.className = "hive-gl-overlay";
viewport.appendChild(canvas);
viewport.appendChild(overlay);
const activations = [];
const scene = window.Hive3D.create({
  canvas, overlay,
  onCellActivate: (runId, rel, cell) => activations.push({ runId, rel, cell }),
  cellRefresh: () => ({}),
});
assert.ok(scene, "WebGL scene initializes against a WebGL-compatible context");
assert.equal(scene.info().renderer, "webgl");
assert.ok(scene.info().objects > 1000, "scene builds the room and repeated workstation geometry");
assert.equal(scene.deskPositions.filter(desk => desk.row === 0).length, 8, "reference rear row has eight workstations");
assert.equal(scene.deskPositions.filter(desk => desk.row === 1).length, 6, "reference front row has six workstations");
assert.equal(scene.monitors.length, scene.deskPositions.length, "each modeled workstation has a monitor overlay");
assert.ok(scene.batchBuffers.opaque.count > 0, "opaque scene geometry is batched");
assert.ok(scene.batchBuffers.transparent.count > 0, "glass, wings and shadows are drawn in a translucent pass");

const cells = Array.from({ length: 14 }, (_, i) => ({
  rel: "steps/" + i + ".log", role: "实现步骤 " + (i + 1),
  status: i === 0 ? "running" : "queued", displayTail: "smoke test",
  displayElapsed: "2s", displayAgent: "test agent",
}));
scene.sync({
  runId: "smoke-run",
  lanes: [
    { name: "规划", count: 14, settled: 0, active: true, cells },
    ...["起草", "评审", "执行", "打磨", "合成"].map(name => ({ name, count: 0, settled: 0, active: false, cells: [] })),
  ],
});
scene.setActive(true);
assert.equal(drawCalls, 0, "reference mode does not waste GPU draws behind the artwork");
assert.ok(uploadedBytes > 100000, "world geometry is uploaded once to GPU buffers");
assert.equal(scene.displayMode, "reference", "high-fidelity reference view is the default presentation");
assert.ok(scene.monitors[0].el.style.width.endsWith("%"), "reference hotspot uses normalized image coordinates");
assert.ok(scene.monitors[0].el.style.height.endsWith("%"), "reference hotspot height is normalized to the artwork");
assert.equal(scene.monitors[0].el.style.left, (80 / 1080 * 100) + "%", "first monitor hotspot aligns with the reference render");
assert.equal(scene.stageMeta[0].el.style.left, (89 / 1080 * 100) + "%", "phase card hotspot aligns with the reference render");
scene.setDisplayMode("live3d");
scene.setActive(true);
assert.ok(drawCalls >= 2, "opaque and translucent batches render in free 3D mode");
assert.equal(scene.displayMode, "live3d", "free 3D mode remains selectable");
assert.ok(scene.monitors[0].el.style.width.endsWith("px"), "free 3D monitor overlay is sized from projected screen bounds");
assert.ok(scene.monitors[0].el.style.height.endsWith("px"), "free 3D monitor overlay has a projected screen height");
scene.monitors[0].el.dispatch("click");
assert.equal(activations.length, 1, "monitor click is wired to the live-log callback");
assert.equal(activations[0].runId, "smoke-run");
assert.equal(activations[0].rel, "steps/0.log");
const previousDrawCalls = drawCalls;
scene.zoomAt(1.18);
assert.ok(drawCalls > previousDrawCalls, "zoom triggers a redraw");
scene.resetView();
scene.dispose();
assert.equal(window.__hive3d, undefined, "dispose releases the global scene reference");
console.log("Hive3D smoke test passed: reference-aligned hotspots, free WebGL projection, geometry batching, clicking, zoom and disposal.");
