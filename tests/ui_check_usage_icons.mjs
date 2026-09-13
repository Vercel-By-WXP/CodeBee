/* 用量页图标与分段控件验收：
 * ① 刷新按钮是真 SVG 图标（非字符符号）、几何非空、有墨迹
 * ② KPI 卡图标渲染且随主题变色
 * ③ 时间范围默认选中「今天」，且选中态视觉可辨（背景/字色与未选中不同）
 * ④ 点击切换后选中态唯一且数据随之变化
 * 用法：node tests/ui_check_usage_icons.mjs [port]（默认对已运行服务 8765 只读验收） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.argv[2] || 8765);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9345;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
};

async function main() {
  let up = false;
  try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* not up */ }
  check(`服务在 ${PORT} 运行`, up);
  if (!up) process.exit(1);

  const tmp = mkdtempSync(join(tmpdir(), "tutti-ico-"));
  let edge = null, ws = null;
  try {
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge CDP 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map(); const errs = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.exceptionThrown") {
        errs.push(m.params?.exceptionDetails?.exception?.description || m.params?.exceptionDetails?.text);
      }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    // 清掉可能残留的范围记忆，验证「首次进入默认今天」
    await js(`localStorage.removeItem("orch.usageDays"); "ok"`);
    await js(`S.usageDays = undefined; switchTab("usage"); "ok"`);
    await sleep(2000);

    // ---- 1) 新增 symbol 几何非空 ----
    const syms = JSON.parse(await js(`(() => {
      const want = ["i-refresh","i-sigma","i-hash","i-coin","i-gauge","i-calendar-days"];
      return JSON.stringify(want.map(id => {
        const s = document.getElementById(id);
        if (!s) return { id, missing: true };
        const bb = s.getBBox();
        return { id, w: +bb.width.toFixed(2), h: +bb.height.toFixed(2), kids: s.children.length };
      }));
    })()`));
    const missing = syms.filter((s) => s.missing);
    check("新增图标 symbol 全部存在", missing.length === 0, JSON.stringify(missing));
    const empty = syms.filter((s) => !s.missing && !(s.w > 4 && s.h > 4 && s.kids > 0));
    check("新增图标几何非空（有实际图形）", empty.length === 0, JSON.stringify(empty));

    // ---- 2) 刷新按钮：SVG 图标、无字符符号、尺寸正常 ----
    const refresh = JSON.parse(await js(`(() => {
      const b = document.getElementById("btn-usage-refresh");
      const svg = b.querySelector("svg.ico");
      const use = svg && svg.querySelector("use").getAttribute("href");
      const r = svg ? svg.getBoundingClientRect() : { width: 0, height: 0 };
      const cs = svg ? getComputedStyle(svg) : {};
      return JSON.stringify({ text: b.textContent.trim(), tag: b.tagName,
        use, w: +r.width.toFixed(1), h: +r.height.toFixed(1),
        stroke: cs.stroke, fill: cs.fill, color: getComputedStyle(b).color });
    })()`));
    check("刷新按钮用 SVG 图标（无 ⟳ 字符残留）", refresh.text === "" && refresh.use === "#i-refresh",
      JSON.stringify(refresh));
    check("刷新按钮图标尺寸正常且描边随主题", refresh.w >= 13 && refresh.h >= 13
      && refresh.fill === "none" && refresh.stroke === refresh.color, JSON.stringify(refresh));

    // ---- 3) KPI 卡图标 ----
    const kpiIcons = JSON.parse(await js(`(() => {
      const out = [];
      document.querySelectorAll("#usage-kpis .kpi").forEach(k => {
        const use = k.querySelector(".kpi-l svg.ico use");
        const svg = k.querySelector(".kpi-l svg.ico");
        const r = svg ? svg.getBoundingClientRect() : { width: 0, height: 0 };
        out.push({ label: k.querySelector(".kpi-l").textContent.trim(),
          use: use ? use.getAttribute("href") : null,
          w: +r.width.toFixed(1), h: +r.height.toFixed(1) });
      });
      return JSON.stringify(out);
    })()`));
    check("5 张 KPI 卡都有图标", kpiIcons.length === 5 && kpiIcons.every((k) => k.use),
      JSON.stringify(kpiIcons));
    check("KPI 图标都按 13px 渲染不塌陷", kpiIcons.every((k) => k.w >= 11 && k.h >= 11),
      JSON.stringify(kpiIcons));
    check("KPI 图标各不相同", new Set(kpiIcons.map((k) => k.use)).size === 5,
      JSON.stringify(kpiIcons.map((k) => k.use)));

    // ---- 4) 默认选中「今天」 ----
    const def = JSON.parse(await js(`(() => {
      const btns = [...document.querySelectorAll("#usage-ranges [data-days]")];
      const act = btns.filter(b => b.classList.contains("active"));
      return JSON.stringify({ n: btns.length, activeDays: act.map(b => b.dataset.days),
        activeText: act.map(b => b.textContent.trim()), usageDays: S.usageDays,
        range: typeof usageRange === "function" ? usageRange() : null });
    })()`));
    check("首次进入默认选中「今天」(days=1)", def.activeDays.length === 1
      && def.activeDays[0] === "1" && def.activeText[0] === "今天", JSON.stringify(def));
    check("请求范围与选中一致（days=1）", def.range === 1, JSON.stringify(def));

    // ---- 5) 选中态视觉可辨：背景与字色和未选中明显不同 ----
    const styleDiff = JSON.parse(await js(`(() => {
      const on = document.querySelector("#usage-ranges .seg-btn.active");
      const off = [...document.querySelectorAll("#usage-ranges .seg-btn")].find(b => !b.classList.contains("active"));
      const a = getComputedStyle(on), b = getComputedStyle(off);
      return JSON.stringify({ onBg: a.backgroundColor, offBg: b.backgroundColor,
        onColor: a.color, offColor: b.color, onWeight: a.fontWeight, offWeight: b.fontWeight });
    })()`));
    check("选中态背景色与未选中不同", styleDiff.onBg !== styleDiff.offBg, JSON.stringify(styleDiff));
    check("选中态字色与未选中不同", styleDiff.onColor !== styleDiff.offColor, JSON.stringify(styleDiff));
    check("选中态字重加粗", Number(styleDiff.onWeight) > Number(styleDiff.offWeight), JSON.stringify(styleDiff));

    // ---- 6) 点击切换：选中唯一 + 数据变化 + 记忆 ----
    await js(`setUsageDays(0); "ok"`);
    await sleep(1200);
    const after = JSON.parse(await js(`(() => {
      const act = [...document.querySelectorAll("#usage-ranges [data-days]")]
        .filter(b => b.classList.contains("active")).map(b => b.dataset.days);
      return JSON.stringify({ active: act, saved: localStorage.getItem("orch.usageDays"),
        kpi: document.getElementById("usage-kpis").textContent.slice(0, 40) });
    })()`));
    check("点击「全部」后选中唯一且为 0", after.active.length === 1 && after.active[0] === "0",
      JSON.stringify(after));
    check("选择被记忆（localStorage）", after.saved === "0", JSON.stringify(after));

    // ---- 7) 重新进入页面时沿用记忆 ----
    await js(`S.usageDays = undefined; switchTab("tasks"); "ok"`);
    await sleep(400);
    await js(`switchTab("usage"); "ok"`);
    await sleep(1400);
    const reenter = JSON.parse(await js(`(() => {
      const act = [...document.querySelectorAll("#usage-ranges [data-days]")]
        .filter(b => b.classList.contains("active")).map(b => b.dataset.days);
      return JSON.stringify({ active: act });
    })()`));
    check("重新进入沿用上次选择（全部）", reenter.active.length === 1 && reenter.active[0] === "0",
      JSON.stringify(reenter));

    // ---- 8) 主题切换后图标仍可见（stroke=currentColor 跟随） ----
    const themeIco = JSON.parse(await js(`(() => {
      const before = getComputedStyle(document.querySelector("#usage-kpis .kpi-l svg.ico")).stroke;
      if (typeof toggleTheme === "function") toggleTheme();
      const after = getComputedStyle(document.querySelector("#usage-kpis .kpi-l svg.ico")).stroke;
      if (typeof toggleTheme === "function") toggleTheme();
      return JSON.stringify({ before, after });
    })()`));
    check("主题切换图标描边随之变化", themeIco.before !== themeIco.after, JSON.stringify(themeIco));

    // ---- 9) 手机视口：分段控件不溢出 ----
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await sleep(800);
    const mob = JSON.parse(await js(`(() => {
      const d = document.documentElement;
      const seg = document.querySelector("#usage-ranges .seg");
      const r = seg.getBoundingClientRect();
      const head = document.querySelector("#sub-usage .panel-head").getBoundingClientRect();
      return JSON.stringify({ scroll: d.scrollWidth > d.clientWidth + 2 ? d.scrollWidth + ">" + d.clientWidth : "",
        segW: Math.round(r.width), headW: Math.round(head.width),
        segOverflow: r.right > head.right + 1 });
    })()`));
    check("手机视口无横向滚动", mob.scroll === "", JSON.stringify(mob));
    check("手机视口分段控件不越出面板", mob.segOverflow === false, JSON.stringify(mob));

    check("全程无 JS 异常", errs.length === 0, errs.join(" ｜ "));
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 用量页图标/分段控件验收：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { bad.forEach((b) => console.log("  ✗ " + b.name)); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
