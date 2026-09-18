/* 发布行几何审查：量 .bm-pub 内所有元素与卡片既有元素（头部按钮/字段/复制钮）
 * 的实际渲染尺寸，输出 JSON 对比表（找视觉不一致，不截图不判断颜色）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18931;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9371;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "pubgeo-"));
  const dataDir = join(tmp, "data");
  const wd = join(tmp, "wd");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-g1"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  mkdirSync(wd, { recursive: true });
  const now = new Date().toISOString().replace("T", " ").slice(0, 19);
  writeFileSync(join(dataDir, "tasks", "pub-g1.json"), JSON.stringify({
    id: "pub-g1", type: "serial_novel", title: "几何审查", goal: "g",
    context: "", workdir: wd, mode: "auto", difficulty: "auto",
    created_at: now, status: "done",
    serial: { chapters: 5, words_per_chapter: 2000, start_chapter: 1 },
    book_meta: { fanqie: { status: "done", at: now, source: "t",
      data: { book_name: "几何审查书", summary: "s", signing_mode: "连载模式",
        category: "都市", tags_theme: ["重生"] } } },
  }), "utf-8");
  writeFileSync(join(wd, "第1章 风起.md"), "# 第1章\n正文".repeat(50), "utf-8");
  writeFileSync(join(dataDir, "runs", "r-g1", "run.json"), JSON.stringify({
    id: "r-g1", kind: "orchestration", title: "几何", task_id: "pub-g1", status: "done",
    steps: [], created_at: now, started_at: now, ended_at: now, cost_usd: 0,
    tokens: 0, error: "", verdict: null, summary: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "publish", "books.json"), JSON.stringify({
    "pub-g1": { fanqie: { book_id: "", title: "几何审查书", url: "", created_at: now } },
  }), "utf-8");

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) { await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {} }
    if (!up) { console.log("SERVICE FAIL"); return; }
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) { await sleep(500);
      try { const l = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = l.find((t) => t.type === "page"); } catch (e) {} }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await evalJs(`(function(){ window.__origFetch = window.fetch.bind(window);
      window.fetch = (u, o) => {
      const s = String(u);
      if (s.includes("/api/publish/task/") && s.includes("/history"))
        return Promise.resolve(new Response(JSON.stringify({ history: [], books: { fanqie: { title: "几何审查书" } } }), { status: 200 }));
      if (s === "/api/publish")
        return Promise.resolve(new Response(JSON.stringify({ platforms: {
          fanqie: { label: "番茄", status: "connected" }, qimao: { label: "七猫", status: "none" } },
          browser_found: true, history: [] }), { status: 200 }));
      return window.__origFetch(u, o); }; })(); true`);
    const geo = await evalJs(`(async () => {
      sideOpenTask("pub-g1");
      for (let i = 0; i < 12; i++) { await new Promise(r => setTimeout(r, 400));
        if (document.querySelectorAll(".bm-pub").length >= 2) break; }
      await new Promise(r => setTimeout(r, 500));
      const m = (el) => { const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return { w: Math.round(r.width), h: Math.round(r.height), fs: cs.fontSize,
                 br: cs.borderRadius, fw: cs.fontWeight, pd: cs.padding }; };
      const head = document.querySelector(".bm-card-head button");
      const pub = [...document.querySelectorAll(".bm-pub")];
      const fq = pub[0], qm = pub[1];
      return JSON.stringify({
        headBtn: head && m(head),
        fqBadge: m(fq.querySelector(".pb-badge")),
        fqBtns: [...fq.querySelectorAll("button")].map(b => ({ t: b.textContent.trim().slice(0, 8), ...m(b) })),
        fqBook: fq.querySelector(".pb-book") && m(fq.querySelector(".pb-book")),
        qmBtns: [...qm.querySelectorAll("button")].map(b => ({ t: b.textContent.trim().slice(0, 8), ...m(b) })),
        copyBtn: m(document.querySelector(".bm-copy")),
        pubRowH: Math.round(fq.getBoundingClientRect().height),
        pubGap: getComputedStyle(fq).gap,
      });
    })()`);
    console.log(geo);
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    await sleep(500);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
