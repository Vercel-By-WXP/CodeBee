/* 品牌纯文字定版（2026-09-15：不放图片）+ 去除内置演示区块的渲染核验：Edge headless + CDP。
 * 1) 品牌区与扫码门为纯文字「CodeBee」，无任何品牌图片。
 * 2) 智能体管理页不再有「内置演示」标题与 ag-mocks 容器。
 * 用法：node tests/ui_logo_verify.mjs （脚本自己起临时服务，端口 18811） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18811;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9341;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-logo-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动（端口 " + PORT + "）", up);

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
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- 1) 品牌区：Codex 式低调纯文字，无磁贴/图片/渐变 ---- */
    const tile = await evalJs(`(() => {
      const brand = document.querySelector('.brand');
      const name = document.querySelector('.brand .brand-name');
      const gate = document.querySelector('.gate-brand');
      if (!brand || !name || !gate) return JSON.stringify({ ok: false });
      return JSON.stringify({ ok: true,
        name: name.textContent.trim(),
        brandDeco: brand.querySelectorAll('img, svg, .logo-tile').length,
        gateDeco: gate.querySelectorAll('img, svg, .logo-tile').length,
        gateText: gate.textContent.trim(),
        bg: getComputedStyle(name).backgroundImage,
        sideSearch: !!document.getElementById('btn-cmdk'),
        kbds: document.querySelectorAll('.side-main .kbd').length,
        expandBtn: !!document.getElementById('btn-side-expand'),
        pbadge: !!document.getElementById('prov-side-badge') });
    })()`);
    const tp = JSON.parse(tile);
    check("品牌字标为纯文字 CodeBee（Codex 式低调）", tp.ok && tp.name === "CodeBee", tile);
    check("品牌区无磁贴/图片/矢量装饰", tp.ok && tp.brandDeco === 0, tile);
    check("扫码门同款纯文字", tp.ok && tp.gateDeco === 0 && tp.gateText === "CodeBee", tile);
    check("字标无渐变（纯正文色）", tp.ok && (tp.bg === "none" || tp.bg === ""), tile);
    check("搜索行（命令面板触发）+ 快捷键提示（N / Ctrl K）", tp.ok && tp.sideSearch && tp.kbds >= 2, tile);
    check("任务树展开/收起按钮 + 待裁决徽章容器", tp.ok && tp.expandBtn && tp.pbadge, tile);
    const oldSprite = await evalJs(`!!document.querySelector('#icon-sprite symbol#i-logo') || !!document.querySelector('#icon-sprite symbol#i-note')`);
    check("旧 #i-logo/#i-note sprite 已移除", !oldSprite);

    /* ---- 3) 智能体管理页：内置演示区块已去除 ---- */
    const agents = await evalJs(`(async () => {
      switchTab('agents');
      await new Promise(r => setTimeout(r, 1200));
      const page = document.querySelector('#sub-agents') || document.body;
      const titles = [...page.querySelectorAll('.sec-title')].map(h => h.textContent.trim());
      return JSON.stringify({ titles, mocks: !!document.getElementById('ag-mocks') });
    })()`);
    const ag = JSON.parse(agents);
    check("「内置演示」标题不再出现", !ag.titles.includes("内置演示"), agents);
    check("ag-mocks 容器已移除", !ag.mocks, agents);
    check("已安装/可安装分组仍在", ag.titles.includes("已安装") && ag.titles.includes("可安装"), agents);

    const fails = results.filter((r) => !r.ok);
    console.log(fails.length ? "\n✗ " + fails.length + " 项未过" : "\n全部通过");
    process.exitCode = fails.length ? 1 : 0;
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(2); });
