/* Fixed-view office: the reference artwork preserves its lighting/materials;
 * localized HTML screens share the same aspect ratio, pan and zoom transform.
 * No WebGL is required. The host supplies actual run/step data and log access. */
window.Hive3D = (function () {
  "use strict";

  const tr = (key, ...args) => typeof window.t === "function" ? window.t(key, ...args) : key;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const STATUS = { running: "运行中", done: "完成", queued: "排队中", failed: "失败", timeout: "超时", cancelled: "已取消" };
  const STAGES = ["规划", "起草", "评审", "执行", "打磨", "合成"];
  // Glass content rectangles measured on the 2048 × 1151 reference image.
  const SCREENS = [
    [163, 441, 127, 55], [389, 441, 126, 55], [616, 441, 125, 55], [843, 441, 125, 55],
    [1073, 441, 122, 55], [1298, 441, 125, 55], [1531, 441, 120, 55], [1750, 441, 125, 55],
    [166, 724, 165, 66], [470, 724, 165, 66], [778, 724, 165, 66],
    [1091, 724, 162, 66], [1392, 724, 163, 66], [1695, 724, 165, 66],
  ];
  const PLATES = [[278, 206], [578, 206], [876, 206], [1175, 206], [1473, 206], [1766, 206]];

  function node(tag, className, parent) {
    const el = document.createElement(tag);
    el.className = className;
    if (parent) parent.appendChild(el);
    return el;
  }

  function ReferenceScene(opts) {
    this.opts = opts;
    this.canvas = opts.canvas;
    this.overlay = opts.overlay;
    this.host = this.canvas.parentElement;
    this.onCellActivate = opts.onCellActivate || function () {};
    this.cellRefresh = opts.cellRefresh || function () { return {}; };
    this.model = null;
    this.page = 0;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.active = false;
    this.timer = null;
    this.screenMeta = [];
    this.listeners = [];
    this.layer = node("div", "hive-reference-layer", this.host);
    this.layer.appendChild(this.host.querySelector(".hive-reference"));
    this.layer.appendChild(this.overlay);
    this.overlay.replaceChildren();
    this.canvas.hidden = true;
    this.host.dataset.renderer = "reference";

    this.stageMeta = PLATES.map(([x, y], index) => {
      const el = node("button", "hg-badge", this.overlay);
      el.type = "button";
      el.style.left = (x / 2048 * 100) + "%";
      el.style.top = (y / 1151 * 100) + "%";
      const name = node("b", "hg-name", el);
      const meta = node("span", "hg-meta", el);
      this.listen(el, "click", () => {
        const lane = this.stageMeta[index].lane;
        if (!lane) return;
        const cell = lane.cells.find((item) => item.status === "running") || lane.cells[lane.cells.length - 1];
        if (cell) this.onCellActivate(this.model.runId, cell.rel, cell);
      });
      return { el, name, meta, lane: null };
    });
    this.monitors = SCREENS.map(([x, y, width, height], index) => {
      const el = node("button", "hg-monitor", this.overlay);
      el.type = "button";
      el.style.left = (x / 2048 * 100) + "%";
      el.style.top = (y / 1151 * 100) + "%";
      el.style.width = (width / 2048 * 100) + "%";
      el.style.height = (height / 1151 * 100) + "%";
      const role = node("b", "hg-monitor-role", el);
      const status = node("span", "hg-monitor-status", el);
      const tail = node("span", "hg-monitor-tail", el);
      const time = node("span", "hg-monitor-time", el);
      this.listen(el, "click", () => {
        const cell = this.screenMeta[index];
        if (cell) this.onCellActivate(this.model.runId, cell.rel, cell);
      });
      return { el, role, status, tail, time };
    });
    const toolbar = this.host.parentElement.querySelector(".hive-scene-tools");
    this.pager = node("div", "hive-scene-pager");
    this.pager.hidden = true;
    this.prev = node("button", "hive-scene-btn", this.pager);
    this.prev.type = "button"; this.prev.textContent = "‹";
    this.pageLabel = node("span", "hive-scene-page-label", this.pager);
    this.next = node("button", "hive-scene-btn", this.pager);
    this.next.type = "button"; this.next.textContent = "›";
    if (toolbar) toolbar.insertBefore(this.pager, toolbar.querySelector(".hive-scene-spacer"));
    this.listen(this.prev, "click", () => this.showPage(this.page - 1));
    this.listen(this.next, "click", () => this.showPage(this.page + 1));
    this.bindSurface();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.host);
    this.languageObserver = new MutationObserver(() => this.updateOverlay());
    this.languageObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
    this.resize();
    this.updateOverlay();
    window.__hive3d = this;
  }

  ReferenceScene.prototype.listen = function (el, type, fn, options) {
    el.addEventListener(type, fn, options);
    this.listeners.push(() => el.removeEventListener(type, fn, options));
  };

  ReferenceScene.prototype.bindSurface = function () {
    let drag = null;
    this.listen(this.host, "contextmenu", (event) => event.preventDefault());
    this.listen(this.host, "pointerdown", (event) => {
      if (!this.active || event.target.closest("button") || event.button !== 0) return;
      drag = { x: event.clientX, y: event.clientY, panX: this.pan.x, panY: this.pan.y };
      this.host.setPointerCapture(event.pointerId);
      this.host.classList.add("is-dragging");
    });
    this.listen(this.host, "pointermove", (event) => {
      if (!drag) return;
      this.pan.x = drag.panX + event.clientX - drag.x;
      this.pan.y = drag.panY + event.clientY - drag.y;
      this.applyTransform();
    });
    const end = () => { drag = null; this.host.classList.remove("is-dragging"); };
    this.listen(this.host, "pointerup", end);
    this.listen(this.host, "pointercancel", end);
    this.listen(this.host, "dblclick", (event) => { if (!event.target.closest("button")) this.resetView(); });
    this.listen(this.host, "wheel", (event) => {
      if (!this.active || event.target.closest("button")) return;
      event.preventDefault();
      this.zoomAt(Math.exp(event.deltaY * 0.0011));
    }, { passive: false });
  };

  ReferenceScene.prototype.sync = function (model) {
    const changedRun = !this.model || this.model.runId !== model.runId;
    this.model = model;
    this.cells = model.lanes.flatMap((lane, stageIdx) => lane.cells.map((cell) => ({ ...cell, stageIdx })));
    if (changedRun) {
      const running = this.cells.findIndex((cell) => cell.status === "running");
      this.page = running < 0 ? 0 : Math.floor(running / SCREENS.length);
      this.resetView();
    }
    this.showPage(this.page);
  };

  ReferenceScene.prototype.showPage = function (page) {
    const pages = Math.max(1, Math.ceil((this.cells || []).length / SCREENS.length));
    this.page = clamp(page, 0, pages - 1);
    this.screenMeta = (this.cells || []).slice(this.page * SCREENS.length, (this.page + 1) * SCREENS.length);
    this.pager.hidden = pages <= 1;
    this.prev.disabled = this.page === 0;
    this.next.disabled = this.page === pages - 1;
    this.pageLabel.textContent = (this.page + 1) + " / " + pages;
    this.updateOverlay();
  };

  ReferenceScene.prototype.updateOverlay = function () {
    const lanes = this.model ? this.model.lanes : [];
    this.stageMeta.forEach((plate, index) => {
      const lane = lanes[index];
      plate.lane = lane || null;
      const name = lane ? tr(lane.nameKey || lane.name) : tr(STAGES[index]);
      plate.name.textContent = tr("阶段 {0} · {1}", index + 1, name);
      plate.meta.textContent = lane ? tr("{0} / {1} 步骤", lane.settled, lane.count) : tr("等待任务");
      plate.el.classList.toggle("hg-active", !!(lane && lane.active));
      plate.el.disabled = !lane || !lane.cells.length;
      plate.el.title = tr("点击查看实时日志");
    });
    this.monitors.forEach((monitor, index) => {
      const cell = this.screenMeta[index];
      const refresh = cell && cell.rel ? this.cellRefresh(cell.rel) || {} : {};
      const state = cell ? cell.status : "idle";
      monitor.el.className = "hg-monitor st-" + state;
      monitor.el.disabled = !cell;
      monitor.el.dataset.log = cell ? cell.rel : "";
      monitor.role.textContent = cell ? tr(cell.role) : tr("空闲工位");
      monitor.status.textContent = cell ? tr(STATUS[state] || "完成") : tr("待命");
      monitor.tail.textContent = cell ? refresh.tail || cell.displayTail ||
        (state === "running" ? tr("等待日志输出…") : state === "queued" ? tr("等待执行") : tr("（无输出）")) : tr("等待任务");
      monitor.time.textContent = cell ? refresh.elapsed || cell.displayElapsed || "" : "";
      monitor.el.title = cell ? [tr(cell.role), tr(STATUS[state] || "完成"), cell.displayAgent,
        monitor.tail.textContent, tr("点击查看实时日志")].filter(Boolean).join(" · ") : tr("空闲工位");
      monitor.el.setAttribute("aria-label", monitor.el.title);
    });
    this.prev.setAttribute("aria-label", tr("上一组工位"));
    this.next.setAttribute("aria-label", tr("下一组工位"));
  };

  ReferenceScene.prototype.setActive = function (active) {
    this.active = !!active;
    this.layer.hidden = !this.active;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.active) {
      this.updateOverlay();
      this.timer = setInterval(() => {
        if (!document.hidden && this.host.offsetParent !== null) this.updateOverlay();
      }, 1000);
    }
  };

  ReferenceScene.prototype.resize = function () {
    this.cssW = this.host.clientWidth;
    this.cssH = this.host.clientHeight;
    this.applyTransform();
  };
  ReferenceScene.prototype.applyTransform = function () {
    const xLimit = this.cssW * (this.zoom - 1) / 2;
    const yLimit = this.cssH * (this.zoom - 1) / 2;
    this.pan.x = clamp(this.pan.x, -xLimit, xLimit);
    this.pan.y = clamp(this.pan.y, -yLimit, yLimit);
    this.layer.style.transform = "translate(" + this.pan.x + "px," + this.pan.y + "px) scale(" + this.zoom + ")";
  };
  ReferenceScene.prototype.zoomAt = function (factor) {
    this.zoom = clamp(this.zoom / factor, 1, 3);
    this.applyTransform();
  };
  ReferenceScene.prototype.resetView = function () {
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.applyTransform();
  };
  ReferenceScene.prototype.info = function () {
    return { renderer: "reference", cells: (this.cells || []).length, screens: SCREENS.length,
      lanes: this.model ? this.model.lanes.length : 0, page: this.page, zoom: this.zoom, cssW: this.cssW, cssH: this.cssH };
  };
  ReferenceScene.prototype.projectCell = function (rel) {
    const index = this.screenMeta.findIndex((cell) => cell.rel === rel);
    if (index < 0) return null;
    const [x, y, width, height] = SCREENS[index];
    return { x: (x + width / 2) / 2048 * this.cssW, y: (y + height / 2) / 1151 * this.cssH };
  };
  ReferenceScene.prototype.dispose = function () {
    this.setActive(false);
    this.resizeObserver.disconnect();
    this.languageObserver.disconnect();
    this.listeners.forEach((remove) => remove());
    this.pager.remove();
    if (window.__hive3d === this) delete window.__hive3d;
  };

  return { create(opts) { return new ReferenceScene(opts); } };
})();
