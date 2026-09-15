/* 探针：主栏页面级容器无框化验证 —— 临时数据目录起服务 + Edge headless CDP。
 * 断言 main 下全部 .panel.wide（composer/运行记录/详情/自动化/设置子页）无边框、
 * 无阴影、透明背景；.panel 基类与内部卡片不受牵连；日夜间各截两张图。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18813;
const SERVICE = `http://127.0.0.1:${PORT}`;
const CDP_PORT = 9339;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "tests", "_out");
mkdirSync(OUT, { recursive: true });

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitService() {
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(SERVICE + "/api/state");
      const body = await res.json();
      if (body) return true;
    } catch (e) { /* 未就绪 */ }
    await sleep(500);
  }
  return false;
}

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-frame-"));
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"],
    { env: { ...process.env, TUTTI_DATA: join(dataDir, "data"), PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore" });
  let edge = null, ws = null;
  try {
    check("服务启动（临时数据目录）", await waitService());

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${mkdtempSync(join(tmpdir(), "tutti-cdp-"))}`,
      `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        const list = await res.json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
      return r.result?.result?.value;
    };
    const shot = async (name) => {
      const r = await send("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(OUT, name), Buffer.from(r.result.data, "base64"));
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    // 页面级容器统一断言：无边框 / 无阴影 / 透明背景
    const targets = {
      composer: `document.querySelector(".composer")`,
      runDetail: `document.getElementById("run-detail")`,
      runsPanel: `document.querySelector("#sub-runs > .panel.wide")`,
      autoPanel: `document.querySelector("#sub-automation > .panel.wide")`,
      usagePanel: `document.querySelector("#sub-usage > .panel.wide")`,
      skinPanel: `document.getElementById("apn-skin")`,
      skillsPanel: `document.querySelector("#sub-orch > .panel.wide")`,
    };
    const styles = await evalJs(`
      (() => {
        const sels = ${JSON.stringify(targets)};
        const out = {};
        for (const [k, expr] of Object.entries(sels)) {
          const el = eval(expr);
          if (!el) { out[k] = "MISSING"; continue; }
          const s = getComputedStyle(el);
          out[k] = { b: s.borderStyle, w: s.borderTopWidth, bg: s.backgroundColor, sh: s.boxShadow };
        }
        return JSON.stringify(out);
      })()`);
    const st = typeof styles === "string" ? JSON.parse(styles) : {};
    for (const [k, label] of Object.entries({
      composer: "新任务 composer", runDetail: "运行详情容器", runsPanel: "运行记录面板",
      autoPanel: "自动化面板", usagePanel: "用量统计面板", skinPanel: "设置·皮肤面板",
      skillsPanel: "编排设置面板",
    })) {
      const v = st[k];
      const ok = v && typeof v === "object";
      check(`${label} 无框化`, ok && v.b === "none" && v.sh === "none" &&
        (v.bg === "rgba(0, 0, 0, 0)" || v.bg === "transparent"),
        ok ? JSON.stringify(v) : v);
    }

    // 对照组 1：.panel 基类不受牵连（动态建一个非 wide 面板，应仍有 1px 边框+底色）
    const base = await evalJs(`
      (() => {
        const d = document.createElement("div");
        d.className = "panel";
        document.body.appendChild(d);
        const s = getComputedStyle(d);
        const r = { w: s.borderTopWidth, bg: s.backgroundColor };
        d.remove();
        return JSON.stringify(r);
      })()`);
    const bv = JSON.parse(base);
    check(".panel 基类不受牵连（1px 边框）", bv.w === "1px", base);

    // 对照组 2：内部卡片类 mgmt-panel 仍有边框（动态建，模拟流程管理弹框里的卡片）
    const card = await evalJs(`
      (() => {
        const d = document.createElement("div");
        d.className = "mgmt-panel";
        document.body.appendChild(d);
        const s = getComputedStyle(d);
        const r = s.borderTopWidth;
        d.remove();
        return r;
      })()`);
    check("内部卡片 mgmt-panel 仍 1px 边框", card === "1px", card);

    // 表单仍可用
    const heroOk = await evalJs(`
      const h = document.querySelector(".composer .hero");
      !!h && h.offsetHeight > 0`);
    check("标题与表单仍正常渲染", heroOk);

    // 截图：夜间 首页 / 运行记录 / 自动化，日间首页
    await shot("frameless_dark_tasks.png");
    await evalJs(`switchTab("runs"); "ok"`);
    await sleep(600);
    await shot("frameless_dark_runs.png");
    await evalJs(`switchTab("automation"); "ok"`);
    await sleep(600);
    await shot("frameless_dark_automation.png");
    await evalJs(`switchTab("tasks");
      document.documentElement.setAttribute("data-theme","light"); "ok"`);
    await sleep(300);
    await shot("frameless_light_tasks.png");

    const pass = results.filter((r) => r.ok).length;
    console.log(`\n${pass}/${results.length} 项通过`);
    if (pass !== results.length) process.exitCode = 1;
  } finally {
    try { ws && ws.close(); } catch (e) { /* 忽略 */ }
    if (edge && edge.pid) spawn("taskkill", ["/pid", String(edge.pid), "/T", "/F"], { stdio: "ignore" });
    if (svc && svc.pid) spawn("taskkill", ["/pid", String(svc.pid), "/T", "/F"], { stdio: "ignore" });
    await sleep(800);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* Windows 句柄延迟 */ }
  }
}
main();
