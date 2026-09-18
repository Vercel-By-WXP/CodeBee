/* 现场复现：直连 8765 打开工作目录选择，抓控制台错误 + 弹框状态（只读操作）。
 * ⚠️ 2026-09-18 起主通道是系统原生对话框（/api/pick_folder）：跑本探针会在
 * 服务所在机器上真弹一个 tkinter 目录窗口，无头环境请改用 ui_workdir_pick.mjs。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CDP_PORT = 9371;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tok = JSON.parse(readFileSync("E:/GoOut/MultiAgentOrchestration/data/remote.json", "utf-8")).token;
  const SERVICE = "http://127.0.0.1:8765/?token=" + tok;
  const tmp = mkdtempSync(join(tmpdir(), "probe-pk-"));
  let edge = null, ws = null;
  const logs = [];
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
    if (!target) { console.log("EDGE FAIL"); return; }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.method === "Runtime.consoleAPICalled") {
        const args = (m.params.args || []).map((a) => a.value ?? a.description ?? "").join(" ");
        logs.push(`[${m.params.type}] ${args.slice(0, 300)}`);
      }
      if (m.method === "Runtime.exceptionThrown") {
        logs.push("[EXC] " + JSON.stringify(m.params.exceptionDetails).slice(0, 500));
      }
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) return "EVAL-ERR: " + JSON.stringify(r.result.exceptionDetails).slice(0, 400);
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(4500);

    console.log("== 页面就绪 ==");
    console.log(await evalJs(`JSON.stringify({
      hasToken: !!localStorage.getItem("orch.token"),
      wd: document.getElementById("f-workdir") ? document.getElementById("f-workdir").value : "(no field)",
      hint: (document.getElementById("f-workdir-hint") || {}).textContent })`));

    console.log("== 点击工作目录输入框打开选择弹框 ==");
    await evalJs(`(() => { const i = document.getElementById("f-workdir"); if (i) i.click(); return 1; })()`);
    await sleep(3000);
    console.log(await evalJs(`JSON.stringify((() => {
      const b = document.getElementById("pk-body");
      return { modalHidden: document.getElementById("modal").classList.contains("hidden"),
        bodyText: b ? b.textContent.slice(0, 200) : "(no body)",
        rows: document.querySelectorAll(".pk-row").length }; })())`));

    console.log("== 手动调 pickerBrowse('__drives__') 看结果 ==");
    console.log(await evalJs(`pickerBrowse("__drives__").then(ok => JSON.stringify({
      ok, bodyText: (document.getElementById("pk-body")||{}).textContent?.slice(0, 150) }))
      .catch(e => "PB-ERR: " + e)`));
    await sleep(1500);

    console.log("== 页内直接 fetch /api/browse 看原始返回 ==");
    console.log(await evalJs(`fetch("/api/browse?path=__drives__", { headers: {
      "X-CodeBee-Token": localStorage.getItem("orch.token") || "" } })
      .then(r => r.text().then(t => JSON.stringify({ status: r.status, body: t.slice(0, 200) })))
      .catch(e => "FETCH-ERR: " + e)`));

    console.log("== 控制台日志（最近 25 条） ==");
    logs.slice(-25).forEach((l) => console.log("  " + l));
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    await sleep(600);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
}
main().catch((e) => { console.error("FATAL", e); process.exit(1); });
