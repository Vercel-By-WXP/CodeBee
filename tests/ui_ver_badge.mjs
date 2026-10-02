/* 多版本序号可见化（OpenCreator「版本化」剩余面）浏览器内单测：
 * artifactsChips 对比 chip 的「与第 N 版对比」标题与 data-prev-ver 透传、
 * 无序号时退回「与上一版对比」；i18n 四键完整性（缺词条会让英文模式露中文）。
 * 直接 evaluate 调页面内全局函数——不造任务不起编排，稳且快。
 * 用法：node tests/ui_ver_badge.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 19400 + (process.pid % 300);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9750 + (process.pid % 400);
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-ver-"));
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪", up);

  const edge = EDGE_CANDIDATES.find((p) => { try { return statSync(p).isFile(); } catch (e) { return false; } });
  if (!edge) { console.log("Edge 不可用，跳过（非 Windows/无 Edge 环境）"); cleanup(svc, null, dataDir); return report(true); }
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-ver-"));
  const proc = spawn(edge, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    // evaluate 结果解包：CDP 响应 {result:{result:{value}}} 两层；异常带出别假失败
    const ev0 = (method) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method }));
    });
    const val = (r) => {
      if (r && r.result && r.result.exceptionDetails)
        throw new Error("页面内异常: " + (r.result.exceptionDetails.exception?.description ||
          JSON.stringify(r.result.exceptionDetails).slice(0, 200)));
      return r && r.result && r.result.result && r.result.result.value;
    };
    const ev = (expr) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method: "Runtime.evaluate",
        params: { expression: expr, returnByValue: true, awaitPromise: true } }));
    });
    await ev0("Runtime.enable");
    await ev0("Page.enable");
    await new Promise((res) => { ws.send(JSON.stringify({ id: ++seq, method: "Page.navigate",
      params: { url: SERVICE + "/" } })); setTimeout(res, 300); });
    await sleep(3500);   // 等 app.js/i18n.js 静态资源加载执行完

    // ① 有序号：对比 chip 标题与 data-prev-ver
    const r1 = await ev(`(() => {
      const h = artifactsChips("run-cur", [{name:"draft.md",size:100}],
        {id:"run-prev", names:new Set(["draft.md"]), ver:3}, false);
      const chip = new DOMParser().parseFromString(h, "text/html")
        .querySelector(".artifact-file-diff");
      return { title: chip && chip.getAttribute("title"),
        ver: chip && chip.getAttribute("data-prev-ver"),
        label: chip && chip.textContent.trim() };
    })()`);
    const v1 = val(r1);
    check("有序号：标题「与第 3 版对比」", v1.title === "与第 3 版对比", v1.title);
    check("有序号：data-prev-ver=3", v1.ver === "3", v1.ver);
    check("有序号：chip 文案仍为「对比」", /对比/.test(v1.label || ""), v1.label);

    // ② 无序号（ver=0，分页 total 缺失的降级路径）：退回通用标题、不带 data-prev-ver 值
    const r2 = await ev(`(() => {
      const h = artifactsChips("run-cur", [{name:"draft.md",size:100}],
        {id:"run-prev", names:new Set(["draft.md"]), ver:0}, false);
      const chip = new DOMParser().parseFromString(h, "text/html")
        .querySelector(".artifact-file-diff");
      return { title: chip && chip.getAttribute("title"),
        ver: chip && chip.getAttribute("data-prev-ver") };
    })()`);
    const v2 = val(r2);
    check("无序号：退回「与上一版对比」", v2.title === "与上一版对比", v2.title);
    check("无序号：data-prev-ver 为空", v2.ver === "", v2.ver);

    // ③ 不同名文件不给对比口（回归：序号化不得改变 diffable 判定）
    const r3 = await ev(`(() => {
      const h = artifactsChips("run-cur", [{name:"new.md",size:50}],
        {id:"run-prev", names:new Set(["draft.md"]), ver:2}, false);
      return new DOMParser().parseFromString(h, "text/html")
        .querySelector(".artifact-file-diff") === null;
    })()`);
    check("上一版无同名文件：不给对比口", val(r3) === true);

    // ④⑤ i18n 完整性（en 模式直接用 t() 探测）：四个新键缺一会让英文界面露中文
    const r4 = await ev(`(() => {
      try { window.setLang("en"); } catch (e) {}
      return { a: t("与第 {0} 版对比", 3), b: t("第 {0} 版", 3),
        c: t("第 {0} 版 → 当前", 3), d: t("该任务第 N 次运行的产出，序号含修订与重试") };
    })()`);
    const v4 = val(r4) || {};
    check("i18n：与第 {0} 版对比", v4.a === "Diff vs version 3", v4.a);
    check("i18n：第 {0} 版", v4.b === "Version 3", v4.b);
    check("i18n：第 {0} 版 → 当前", v4.c === "Version 3 → current", v4.c);
    check("i18n：徽章悬停说明", /Nth run/.test(v4.d || ""), v4.d);

    // ⑥ 英文下 chips 标题整体形态
    const r5 = await ev(`(() => {
      const h = artifactsChips("run-cur", [{name:"draft.md",size:100}],
        {id:"run-prev", names:new Set(["draft.md"]), ver:3}, false);
      const chip = new DOMParser().parseFromString(h, "text/html")
        .querySelector(".artifact-file-diff");
      return chip && chip.getAttribute("title");
    })()`);
    check("英文：标题 Diff vs version 3", val(r5) === "Diff vs version 3", val(r5));

    ws.close();
  } finally {
    cleanup(svc, proc, dataDir, profile);
  }
  return report();
}

function cleanup(svc, proc, ...dirs) {
  try { proc && proc.kill(); } catch (e) { /* 已退 */ }
  try { svc && svc.kill(); } catch (e) { /* 已退 */ }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch (e) { /* Windows 句柄延迟 */ } }
}
function report(early) {
  const bad = results.filter((r) => !r.ok).length;
  console.log(early ? "（Edge 缺席，早退）" : "");
  console.log(bad ? `✗ ${bad} 项失败` : "✓ 全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });