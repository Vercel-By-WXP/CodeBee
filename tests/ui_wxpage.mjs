/* 群摘要设置子页端到端（2026-09-21 用户反馈「选不中 python、加载不了群」）：
 * Edge headless + CDP 真点：切到群摘要子页 → 扫描 Python → 候选出现并点选
 * 回填 → 保存后轮询不冲掉 → 悬浮 dock 已移除 → 刷新群列表有响应（错误人话
 * 或群列表都算通，环境依赖微信登录态）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18800 + (process.pid % 300);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9500 + (process.pid % 400);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-wxpage-"));
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-wxpage-"));
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

    // 1) 切到群摘要子页
    await evalJs(`switchTab("__wxdigest"); "ok"`);
    await sleep(600);
    const page = JSON.parse(await evalJs(`JSON.stringify({
      visible: !document.getElementById("sub-__wxdigest").classList.contains("hidden"),
      hasScan: !!document.getElementById("bee-py-scan"),
      dockGone: !document.getElementById("bee-dock") && !document.getElementById("bee-panel")
    })`));
    check("群摘要子页显示（正式页面）", page.visible);
    check("扫描按钮在位", page.hasScan);
    check("右下角悬浮坞已移除", page.dockGone);

    // 2) 点「扫描」→ 轮询候选出现（真机真扫，本机至少有一个 python）
    await evalJs(`document.getElementById("bee-py-scan").click(); "ok"`);
    let cand = null;
    for (let i = 0; i < 45 && !cand; i++) {
      await sleep(1000);
      cand = await evalJs(`(() => {
        const items = document.querySelectorAll("#bee-py-cands .bee-py-item");
        return items.length ? JSON.stringify(Array.from(items).map((b) => b.textContent)) : null;
      })()`);
    }
    check("扫描出候选解释器", !!cand, "等待超时");
    const candList = cand ? JSON.parse(cand) : [];
    console.log("    候选:", candList.join(" ┃ ").slice(0, 200));

    // 3) 点选候选 → 输入框回填；即使保存前撞上轮询也不被旧配置冲掉
    const picked = await evalJs(`
      (() => {
        const first = document.querySelector("#bee-py-cands .bee-py-item");
        if (!first) return "no-cand";
        first.click();
        const inp = document.getElementById("bee-reader-py");
        return inp.value || "empty";
      })()`);
    check("点选候选回填输入框", picked !== "no-cand" && picked !== "empty" && picked.length > 4, picked);
    await evalJs(`beeRefresh(); "ok"`);
    await sleep(1200);
    const keptBeforeSave = await evalJs(`document.getElementById("bee-reader-py").value`);
    check("保存前轮询不冲掉已选路径", keptBeforeSave === picked, keptBeforeSave + " vs " + picked);
    await evalJs(`document.getElementById("bee-save").click(); "ok"`);
    await sleep(1200);
    await evalJs(`beeRefresh(); "ok"`);
    await sleep(1500);
    const kept = await evalJs(`document.getElementById("bee-reader-py").value`);
    check("保存后轮询不冲掉已选路径", kept === picked, kept + " vs " + picked);

    // 4) 勾选微信直连 → 「刷新群列表」有响应（真 sidecar：错误也说明链路通）
    await evalJs(`
      (async () => {
        const rd = document.getElementById("bee-reader");
        rd.checked = true; rd.dispatchEvent(new Event("change", { bubbles: true }));
        document.getElementById("bee-groups-refresh").click();
        return "ok";
      })()`);
    let groupResp = "";
    for (let i = 0; i < 20 && !groupResp; i++) {
      await sleep(1000);
      groupResp = await evalJs(`(() => {
        const t = document.getElementById("toast")?.textContent || "";
        const list = document.getElementById("bee-groups-list")?.textContent || "";
        return /刷新群列表失败|读取群列表|微信|失败|错误/.test(t + list) || list.length > 20 ? (t + "‖" + list).slice(0, 200) : "";
      })()`);
    }
    // 环境依赖微信登录态：能拿到「人话错误」或真群列表都算链路通
    check("刷新群列表有响应（错误为人话/或真列表）", !!groupResp, groupResp || "60s 无响应");
    if (groupResp) console.log("    群列表响应:", groupResp.slice(0, 160));

    // 5) 桌宠直达锚点：#goto=__wxdigest 自动切子页（新页面验证）
    await send("Page.navigate", { url: SERVICE + "/#goto=__wxdigest" });
    await sleep(4000);
    const goto = await evalJs(`!document.getElementById("sub-__wxdigest").classList.contains("hidden")`);
    check("桌宠锚点 #goto=__wxdigest 自动切到群摘要页", goto === true);
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
