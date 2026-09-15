/* 工作目录「点输入框/选择…」弹框核验（Edge headless + CDP，临时端口 18831）：
 * A) 「选择…」按钮与点输入框都能打开选择弹框；
 * B) 目录行写回的是绝对路径（服务端只给目录名，前端拼接）；
 * C) 上级/此电脑导航、使用当前目录、行内「选这个」；
 * D) 选定后 input/change 事件触发（git 探测联动）。 */
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

    const modalState = `JSON.stringify((() => { const m = document.getElementById("modal");
      const rows = [...document.querySelectorAll("#pk-body .pk-row")];
      const pb = document.getElementById("pk-body");
      return { open: m ? !m.classList.contains("hidden") : false,
        title: (document.getElementById("modal-title")||{}).textContent || "",
        path: (document.querySelector("#pk-body .pk-path")||{}).textContent || "",
        rows: rows.map(r => ({ p: r.dataset.p, hasBtn: !!r.querySelector("button") })),
        bodyH: pb ? Math.round(pb.getBoundingClientRect().height) : 0,
        foot: (document.getElementById("modal-foot")||{}).textContent || "" }; })())`;

    /* A1) 「选择…」按钮打开弹框（预填 sandbox → 直读该目录） */
    await evalJs(`(() => {
      const inp = document.getElementById("f-workdir");
      inp.value = ${JSON.stringify(sandbox)};
      inp.dispatchEvent(new Event("change", { bubbles: true }));
      let fired = 0;
      inp.addEventListener("input", () => { fired++; });
      window.__wdEvents = () => fired;
      return 1; })()`);
    await evalJs(`(() => { const b = [...document.querySelectorAll("button")]
      .find(x => x.getAttribute("onclick") === "pickFolder('f-workdir')");
      if (b) b.click(); return 1; })()`);
    await sleep(900);
    let m = JSON.parse(await evalJs(modalState));
    const hOneRow = m.bodyH;
    check("「选择…」按钮：弹框打开且直读预填目录", m.open && m.path === sandbox, JSON.stringify(m).slice(0, 220));
    check("子目录行：绝对路径 + 行内按钮", m.rows.length === 1 && m.rows[0].p === join(sandbox, "a") && m.rows[0].hasBtn,
      JSON.stringify(m.rows));

    /* B) 行内「选这个」→ 写回绝对路径 + input/change 联动 */
    await evalJs(`(() => { document.querySelector("#pk-body .pk-row button").click(); return 1; })()`);
    await sleep(400);
    const after = JSON.parse(await evalJs(`JSON.stringify({
      v: document.getElementById("f-workdir").value,
      open: !document.getElementById("modal").classList.contains("hidden"),
      events: window.__wdEvents() })`));
    check("「选这个」：写回绝对路径且弹框关闭",
      after.v === join(sandbox, "a") && !after.open, JSON.stringify(after));
    check("选定触发 input 事件（git 探测联动）", after.events >= 1, "events=" + after.events);

    /* A2) 点输入框直接打开（本次用户要的主行为） */
    await evalJs(`document.getElementById("f-workdir").click(); "ok"`);
    await sleep(900);
    m = JSON.parse(await evalJs(modalState));
    check("点输入框：直接打开选择弹框（落在当前值目录）", m.open && m.path === join(sandbox, "a"),
      JSON.stringify(m).slice(0, 220));

    /* C) 上级 → 此电脑 → 盘符行 */
    await evalJs(`(() => { const b = [...document.querySelectorAll("#pk-body button")]
      .find(x => x.textContent === "上级"); if (b) b.click(); return 1; })()`);
    await sleep(700);
    m = JSON.parse(await evalJs(modalState));
    check("上级：回到 sandbox 且两行子目录可见", m.path === sandbox && m.rows.map(r => r.p).includes(join(sandbox, "a")),
      JSON.stringify(m).slice(0, 220));
    await evalJs(`(() => { const b = [...document.querySelectorAll("#pk-body button")]
      .find(x => x.textContent === "此电脑"); if (b) b.click(); return 1; })()`);
    await sleep(700);
    m = JSON.parse(await evalJs(modalState));
    check("此电脑：列出盘符行", m.path === "此电脑" && m.rows.some(r => /^[A-Za-z]:\\$/.test(r.p)),
      JSON.stringify(m).slice(0, 220));
    check("弹框固定高度：多行盘符列表与单行目录等高", m.bodyH === hOneRow && m.bodyH > 300,
      "单行=" + hOneRow + " 盘符=" + m.bodyH);

    /* C2) 使用当前目录：进入某盘根目录后收下 */
    await evalJs(`(() => { const row = [...document.querySelectorAll("#pk-body .pk-row")]
      .find(r => r.dataset.p === "C:\\\\"); if (row) row.click(); return 1; })()`);
    await sleep(1200);
    await evalJs(`(() => { const b = [...document.querySelectorAll("#modal-foot button")]
      .find(x => x.textContent === "使用当前目录"); if (b) b.click(); return 1; })()`);
    await sleep(400);
    const useCur = JSON.parse(await evalJs(`JSON.stringify({
      v: document.getElementById("f-workdir").value,
      open: !document.getElementById("modal").classList.contains("hidden") })`));
    check("进入 C:\\ 后「使用当前目录」：写回盘根且弹框关闭", useCur.v === "C:\\" && !useCur.open,
      JSON.stringify(useCur));

    /* D) i18n：英文模式按钮变英文 */
    await evalJs(`localStorage.setItem("orch.lang","en");applyI18n&&applyI18n();
      document.getElementById("f-workdir").click(); "ok"`);
    await sleep(900);
    m = JSON.parse(await evalJs(modalState));
    check("英文模式：弹框标题/按钮翻译",
      m.open && m.title === "Choose folder" && m.foot.includes("Use this folder"),
      JSON.stringify({ title: m.title, foot: m.foot }));
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
