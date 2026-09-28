/* 自动化表单「会话延续」开关（fresh_chat）：Edge headless + CDP。
 * 默认延续上一轮（不勾）、勾选=每次新聊天；断言走 DOM 状态与真实 API 往返。
 * 临时数据目录 + 独立端口，不碰真实 data/ 与 8765。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18830;
const CDP_PORT = 9355;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-autofresh-"));
  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(" + SERVICE_PORT + ")", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown") {
        const d = msg.params.exceptionDetails || {};
        const line = (d.exception && (d.exception.description || d.exception.value)) || d.text;
        consoleErrors.push(String(line).split("\n").slice(0, 3).join(" / "));
      }
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    const R = await evalJson(`(async () => {
      const $ = (id) => document.getElementById(id);
      const out = {};
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));

      switchTab("automation");
      await wait(600);

      // 1) 新建表单：开关在、默认不勾（延续档）、说明文案在
      await autoForm();
      await wait(200);
      out.hasToggle = !!$("au-fresh");
      out.defaultOff = !$("au-fresh").checked;
      out.hintShown = ($("modal-body").textContent || "").includes("延续");

      // 2) 勾选「每次新聊天」保存 → 真实 API 落库 fresh_chat=true
      $("au-name").value = "延续巡检";
      $("au-prompt").value = "检查仓库状态";
      $("au-fresh").checked = true;
      await saveAutoForm("");
      await wait(500);
      out.modalClosed = $("modal").classList.contains("hidden");
      const list = await fetch("/api/automation").then((r) => r.json());
      const t = (list.tasks || [])[0] || {};
      out.savedFresh = t.fresh_chat;
      await loadAutomation();
      await wait(300);
      out.cardFreshText = document.querySelector("#auto-list .card")?.textContent || "";

      // 3) 编辑回来：预勾选；取消勾选保存 → 落库 false
      await autoForm(t);
      await wait(200);
      out.editChecked = $("au-fresh").checked;
      $("au-fresh").checked = false;
      await saveAutoForm(t.id);
      await wait(400);
      const list2 = await fetch("/api/automation").then((r) => r.json());
      out.updatedFresh = ((list2.tasks || [])[0] || {}).fresh_chat;
      await loadAutomation();
      await wait(300);
      out.cardCarryText = document.querySelector("#auto-list .card")?.textContent || "";

      // 4) 英文模式：开关标签与两张卡片 tag 都要换语言
      if (typeof setLang === "function") {
        setLang("en");
        await wait(400);
        await autoForm();
        await wait(200);
        const labels = Array.from(document.querySelectorAll("#modal-body label"))
          .map((x) => x.textContent.trim());
        out.enToggleLabel = labels.includes("Start a fresh chat on every run");
        closeModal();
        await wait(200);
        await loadAutomation();
        await wait(300);
        const cardEn = document.querySelector("#auto-list .card")?.textContent || "";
        out.enCardTag = cardEn.includes("Continues from last run");
        setLang("zh");
        await wait(300);
      }
      return out;
    })()`);

    if (!R || R.__err) { check("浏览器端评估执行", false, JSON.stringify(R)); }
    else {
      check("开关存在", R.hasToggle);
      check("默认不勾（延续档）", R.defaultOff);
      check("延续说明文案在", R.hintShown);
      check("保存后弹框关闭", R.modalClosed);
      check("落库 fresh_chat=true", R.savedFresh === true, String(R.savedFresh));
      check("卡片显示「每次新聊天」", (R.cardFreshText || "").includes("每次新聊天"), R.cardFreshText);
      check("编辑表单预勾选", R.editChecked);
      check("取消后落库 fresh_chat=false", R.updatedFresh === false, String(R.updatedFresh));
      check("卡片显示「延续上轮」", (R.cardCarryText || "").includes("延续上轮"), R.cardCarryText);
      check("英文模式：开关标签已译", R.enToggleLabel !== false, String(R.enToggleLabel));
      check("英文模式：卡片 tag 已译", R.enCardTag !== false, String(R.enCardTag));
    }
    check("无未捕获 JS 异常", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 200));
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(600);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\nFAILED ${bad}/${results.length}` : `\nOK ${results.length}/${results.length}`);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
