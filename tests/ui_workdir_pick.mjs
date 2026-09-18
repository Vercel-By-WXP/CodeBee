/* 工作目录「点输入框/选择…」核验（Edge headless + CDP，临时端口 18831）：
 * 主通道是系统原生「选择文件夹」对话框（服务端 tkinter 子进程），无头测试里
 * 不能真弹，页面内 stub 掉 /api/pick_folder，验证前端四条分支：
 * A) 原生成功：路径直接回填输入框 + input/change 事件联动，不弹网页弹框；
 * B) 点输入框同走原生；C) 取消（空 path）写回原值；D) busy 提示；
 * E) fallback（机器无 tkinter）：回落网页目录弹框——预填直读、「选这个」写回、
 *    弹框固定高度、英文标题。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18831;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9353;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-wdpick-"));
  const dataDir = join(tmp, "data");
  const sandbox = join(dataDir, "sandbox-workdir");
  mkdirSync(join(sandbox, "a", "b"), { recursive: true });
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);

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
    await send("Runtime.evaluate", { expression: `window.alert=()=>true;window.confirm=()=>true;` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* stub：/api/pick_folder 按应答队列回包，其余请求照走原 fetch；请求体留档断言 */
    await evalJs(`(() => {
      const orig = window.fetch.bind(window);
      window.__pickReplies = [];
      window.__pickReqs = [];
      window.fetch = (url, opts) => {
        if (String(url).includes("/api/pick_folder")) {
          window.__pickReqs.push(String((opts || {}).body || ""));
          const reply = window.__pickReplies.length ? window.__pickReplies.shift() : { path: "" };
          return Promise.resolve(new Response(JSON.stringify(reply),
            { status: 200, headers: { "Content-Type": "application/json" } }));
        }
        return orig(url, opts);
      };
      let fired = 0;
      document.getElementById("f-workdir").addEventListener("input", () => { fired++; });
      window.__wdEvents = () => fired;
      return 1;
    })()`);

    const pickBtn = `(() => { const b = [...document.querySelectorAll("button")]
      .find(x => x.getAttribute("onclick") === "pickFolder('f-workdir')");
      if (!b) throw new Error("选择…按钮不存在"); b.click(); return 1; })()`;
    const inputState = `JSON.stringify({
      v: document.getElementById("f-workdir").value,
      open: !document.getElementById("modal").classList.contains("hidden"),
      events: window.__wdEvents(),
      toast: (document.getElementById("toast") || {}).textContent || "" })`;

    /* A) 原生成功：路径直接回填，不弹网页弹框 */
    await evalJs(`(() => {
      const inp = document.getElementById("f-workdir");
      inp.value = ${JSON.stringify(sandbox)};
      inp.dispatchEvent(new Event("change", { bubbles: true }));
      window.__pickReplies.push({ path: ${JSON.stringify(join(sandbox, "a"))} });
      return 1; })()`);
    await evalJs(pickBtn);
    await sleep(400);
    let st = JSON.parse(await evalJs(inputState));
    check("原生成功：写回绝对路径且不弹网页弹框",
      st.v === join(sandbox, "a") && !st.open, JSON.stringify(st));
    check("选定触发 input 事件（git 探测联动）", st.events >= 1, "events=" + st.events);
    const req0 = JSON.parse(await evalJs(`window.__pickReqs[0] || "null"`));
    check("原生请求带上预填目录与标题", req0 && req0.initial === sandbox && req0.title === "选择文件夹",
      JSON.stringify(req0));

    /* B) 点输入框同走原生：initial 取当前值，回包直接写回 */
    await evalJs(`(() => { window.__pickReplies.push({ path: ${JSON.stringify(sandbox)} }); return 1; })()`);
    await evalJs(`document.getElementById("f-workdir").click(); "ok"`);
    await sleep(400);
    st = JSON.parse(await evalJs(inputState));
    const req1 = JSON.parse(await evalJs(`window.__pickReqs[1] || "null"`));
    check("点输入框：回填新路径且请求 initial=当前值",
      st.v === sandbox && !st.open && req1 && req1.initial === join(sandbox, "a"),
      JSON.stringify({ st, req1 }));

    /* C) 取消（空 path）：原值不动 */
    await evalJs(`(() => { window.__pickReplies.push({ path: "" }); return 1; })()`);
    await evalJs(pickBtn);
    await sleep(400);
    st = JSON.parse(await evalJs(inputState));
    check("取消：写回值不变且不弹框", st.v === sandbox && !st.open, JSON.stringify(st));

    /* D) 已有对话框在等（busy）：提示且不弹框 */
    await evalJs(`(() => { window.__pickReplies.push({ path: "", busy: true }); return 1; })()`);
    await evalJs(pickBtn);
    await sleep(400);
    st = JSON.parse(await evalJs(inputState));
    check("busy：toast 提示且不弹框",
      st.toast.includes("已有一个选择窗口正在等待") && !st.open, JSON.stringify(st));

    /* E) fallback（无 tkinter）：回落网页目录弹框 */
    await evalJs(`(() => { window.__pickReplies.push({ path: "", fallback: true }); return 1; })()`);
    await evalJs(pickBtn);
    await sleep(900);
    let m = JSON.parse(await evalJs(`JSON.stringify({
      open: !document.getElementById("modal").classList.contains("hidden"),
      title: (document.getElementById("modal-title") || {}).textContent || "",
      path: (document.querySelector("#pk-body .pk-path") || {}).textContent || "",
      rows: [...document.querySelectorAll("#pk-body .pk-row")].map(r => r.dataset.p),
      bodyH: Math.round(document.getElementById("pk-body").getBoundingClientRect().height),
      foot: (document.getElementById("modal-foot") || {}).textContent || "" })`));
    const hOneRow = m.bodyH;
    check("fallback：弹框打开且直读预填目录", m.open && m.path === sandbox, JSON.stringify(m).slice(0, 220));
    check("子目录行：绝对路径 + 行内按钮", m.rows.length === 1 && m.rows[0] === join(sandbox, "a"),
      JSON.stringify(m.rows));
    await evalJs(`(() => { document.querySelector("#pk-body .pk-row button").click(); return 1; })()`);
    await sleep(400);
    st = JSON.parse(await evalJs(inputState));
    check("「选这个」：写回绝对路径且弹框关闭", st.v === join(sandbox, "a") && !st.open, JSON.stringify(st));

    /* E2) 弹框固定高度：多行盘符列表与单行目录等高（CSS 回归） */
    await evalJs(`(() => { window.__pickReplies.push({ path: "", fallback: true }); return 1; })()`);
    await evalJs(pickBtn);
    await sleep(500);
    await evalJs(`(async () => { await pickerBrowse("__drives__"); return 1; })()`);
    await sleep(700);
    m = JSON.parse(await evalJs(`JSON.stringify({
      path: (document.querySelector("#pk-body .pk-path") || {}).textContent || "",
      rows: [...document.querySelectorAll("#pk-body .pk-row")].length,
      bodyH: Math.round(document.getElementById("pk-body").getBoundingClientRect().height) })`));
    check("盘符列表可用", m.path === "此电脑" && m.rows >= 1, JSON.stringify(m));
    check("弹框固定高度：多行盘符列表与单行目录等高", m.bodyH === hOneRow && m.bodyH > 300,
      "单行=" + hOneRow + " 盘符=" + m.bodyH);
    await evalJs(`(async () => { await pickerBrowse(${JSON.stringify(sandbox)}); return 1; })()`);
    await sleep(500);
    await evalJs(`(() => { document.querySelector("#pk-body .pk-row button").click(); return 1; })()`);
    await sleep(300);

    /* F) 英文模式：回落弹框标题/按钮翻译 */
    await evalJs(`localStorage.setItem("orch.lang","en");applyI18n&&applyI18n();
      window.__pickReplies.push({ path: "", fallback: true });
      (() => { const b = [...document.querySelectorAll("button")]
        .find(x => x.getAttribute("onclick") === "pickFolder('f-workdir')"); b.click(); return 1; })(); "ok"`);
    await sleep(900);
    m = JSON.parse(await evalJs(`JSON.stringify({
      open: !document.getElementById("modal").classList.contains("hidden"),
      title: (document.getElementById("modal-title") || {}).textContent || "",
      foot: (document.getElementById("modal-foot") || {}).textContent || "" })`));
    check("英文模式：回落弹框标题/按钮翻译",
      m.open && m.title === "Choose folder" && m.foot.includes("Use this folder"),
      JSON.stringify(m));
    await evalJs(`localStorage.setItem("orch.lang","zh"); "ok"`);
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { if (svc) svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
