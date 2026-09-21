/* 插件市场分区+启停开关 UI 验证（借鉴 ZCode 插件管理形态）：
 * 分区标题三组渲染、已装卡片开关存在、切换开关走 pack-op 且轮询不冲掉、
 * 分区模式外层块级（标题不当格子排——样式乱修复）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 19400 + (process.pid % 300);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9800 + (process.pid % 400);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-mkgroup-"));
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-mk-"));
  const proc = spawn(EDGE, [
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
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    // 1) 切到插件市场页（全新库：已安装组为空，分区只有 内置/可安装 两组——
    //    空组不渲染是既定行为）
    await evalJs(`switchTab("market"); "ok"`);
    await sleep(1200);
    const s0 = JSON.parse(await evalJs(`JSON.stringify({
      grouped: document.getElementById("mk-grid").classList.contains("mk-grouped"),
      titles: Array.from(document.querySelectorAll("#mk-grid > .sec-title")).map((h) => h.textContent.trim()),
      blocks: document.querySelectorAll("#mk-grid > .mk-grid").length
    })`));
    check("分区模式：外层块级+组标题渲染", s0.grouped && s0.titles.length >= 2 && s0.blocks === s0.titles.length,
      JSON.stringify(s0));
    check("分区首组是「内置」", (s0.titles[0] || "").startsWith("内置"), JSON.stringify(s0.titles));

    // 2) 装一个 → 重渲染后「已安装」组出现，卡片带默认启用的开关
    await evalJs(`mkInstall("git-workflow"); "ok"`);
    await sleep(2000);
    const sw = JSON.parse(await evalJs(`JSON.stringify({
      titles: Array.from(document.querySelectorAll("#mk-grid > .sec-title")).map((h) => h.textContent.trim()),
      installedSwitches: Array.from(document.querySelectorAll("#mk-grid > .mk-grid"))
        .filter((g) => g.previousElementSibling && g.previousElementSibling.textContent.startsWith("已安装"))
        .map((g) => g.querySelectorAll(".switch input").length)[0] || 0
    })`));
    check("安装后「已安装」组出现且开关存在", sw.installedSwitches === 1, JSON.stringify(sw));

    // 3) 点开关停用 → pack-op 生效（/api/skills packs enabled=false）
    await evalJs(`
      (() => {
        const g = Array.from(document.querySelectorAll("#mk-grid > .mk-grid"))
          .find((g) => g.previousElementSibling && g.previousElementSibling.textContent.startsWith("已安装"));
        const sw = g && g.querySelector(".switch input");
        if (sw) sw.click();
        return sw ? "clicked" : "no-switch";
      })()`);
    await sleep(1200);
    const dis = await evalJs(`
      (async () => {
        const v = await (await fetch("/api/skills", { headers: authHeaders() })).json();
        const g = (v.packs || []).find((p) => (p.name || "").includes("Git"));
        return JSON.stringify({ enabled: g ? g.enabled : null });
      })()`);
    check("停用落到经验库（enabled=false）", dis && JSON.parse(dis).enabled === false, dis);

    // 4) 切回英文：分区标题词条不空
    await evalJs(`window.setLang && window.setLang("en"); renderMarket(); "ok"`);
    const en = await evalJs(`document.getElementById("mk-grid").textContent.includes("Built-in")`);
    check("英文分区标题渲染", en === true);
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }

  const fails = results.filter((r) => !r.ok);
  console.log(fails.length ? `\n${fails.length}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
