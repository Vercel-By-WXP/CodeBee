/* 已发章数平台校准 UI 回归（2026-09-28 校准案）：Edge headless + CDP。
 * 1) 番茄卡带 remote_*（刚校准过）→「发一章（已发 8）」吃平台实况而非本地台账 7，
 *    tooltip 带「平台实况 8 / 本地台账 7 / 校准于」；
 * 2) 七猫卡已登记但从未校准 → 显示本地台账口径「已发 1」，
 *    且打开作品页后自动触发一次 sync-published（body 只带 platform，无 manual）；
 * 3) 已校准平台（remote_synced_at 新鲜）不重复自动触发。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18934;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9377;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "codebee-pubsync-"));
  const dataDir = join(tmp, "data");
  const wd = join(tmp, "wd");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-sync-1"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  mkdirSync(wd, { recursive: true });
  const now = timeStr();

  writeFileSync(join(dataDir, "tasks", "sync-t1.json"), JSON.stringify({
    id: "sync-t1", type: "serial_novel", title: "校准回归", goal: "测试",
    context: "", workdir: wd, mode: "auto", difficulty: "auto",
    created_at: now, status: "done",
    serial: { chapters: 8, words_per_chapter: 2000, start_chapter: 1 },
    book_meta: {
      fanqie: { status: "done", at: now, source: "测试",
        data: { book_name: "测试书", summary: "简介", signing_mode: "连载模式",
          category: "都市", tags_theme: ["重生"] } },
    },
  }), "utf-8");
  writeFileSync(join(wd, "第1章 风起.md"), "# 第1章 风起\n\n" + "正文".repeat(400), "utf-8");
  writeFileSync(join(dataDir, "runs", "r-sync-1", "run.json"), JSON.stringify({
    id: "r-sync-1", kind: "orchestration", title: "校准回归", task_id: "sync-t1",
    entry_id: null, op: null, status: "done",
    steps: [{ n: 1, role: "impl", agent: "claude", agent_label: "Claude Code", note: "",
      status: "done", started_at: "00:00:01", ended_at: null, duration_s: 1, exit_code: 0,
      summary: "完成", log: null, cost_usd: 0, tokens: 0 }],
    created_at: now, started_at: now, ended_at: now, cost_usd: 0, tokens: 0,
    error: "", verdict: null, summary: "",
  }), "utf-8");

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
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    // stub：番茄=已校准（remote 8 / 本地 7，synced_at 新鲜→不重复自动触发）；
    // 七猫=已连接已登记、从未校准（→自动触发一次）。synced_at 用页面当前时间。
    await evalJs(`(window.__pbPosts=[]); (function(){
      window.alert = () => true; window.confirm = () => true;
      const nowStr = new Date().toLocaleString("sv-SE").replace("T", " ");
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/api/publish/task/") && u.includes("/history")) {
          return new Response(JSON.stringify({
            history: [
              { platform: "fanqie", action: "upload_chapter", chapter_no: 7, ok: true, ts: nowStr },
              { platform: "fanqie", action: "upload_chapter", chapter_no: 7, ok: true, ts: nowStr },
              { platform: "qimao", action: "upload_chapter", chapter_no: 1, ok: true, ts: nowStr },
            ],
            books: {
              fanqie: { book_id: "777", title: "测试书", url: "", created_at: "",
                remote_total: 8, remote_published: 8, remote_review: 0,
                remote_synced_at: nowStr },
              qimao: { book_id: "888", title: "测试书", url: "", created_at: "" },
            },
            published: { fanqie: 7, qimao: 1 },
          }), { status: 200 });
        }
        if (u === "/api/publish" || u.startsWith("/api/publish?")) {
          return new Response(JSON.stringify({
            platforms: {
              fanqie: { label: "番茄", status: "connected", at: "", error: "", last_action: "" },
              qimao: { label: "七猫", status: "connected", at: "", error: "", last_action: "" },
            }, browser_found: true, history: [],
          }), { status: 200 });
        }
        if (u.includes("/api/publish/") && (opts || {}).method === "POST") {
          window.__pbPosts.push({ url: u, body: (opts.body || "") });
          return new Response(JSON.stringify({ ok: true, started: [] }), { status: 200 });
        }
        return orig(url, opts);
      };
    })(); true`);

    const s1 = JSON.parse(await evalJs(`(async () => {
      sideOpenTask("sync-t1");
      for (let i = 0; i < 14; i++) {
        await new Promise(r2 => setTimeout(r2, 500));
        if (document.querySelectorAll(".bm-pub").length >= 2) break;
      }
      // 自动校准是打开作品页后 pbSyncState 轮询发出，等它落进 __pbPosts
      for (let i = 0; i < 16; i++) {
        await new Promise(r2 => setTimeout(r2, 500));
        if (window.__pbPosts.some(p => p.url.includes("sync-published"))) break;
      }
      await new Promise(r2 => setTimeout(r2, 400));
      const btnInfo = [...document.querySelectorAll(".bm-pub")].map(x => {
        const pub = [...x.querySelectorAll("button")].find(b => b.textContent.includes("发一章"));
        return { btns: [...x.querySelectorAll("button")].map(b => b.textContent.trim()).join("|"),
                 title: pub ? pub.title : "" };
      });
      return JSON.stringify({ n: document.querySelectorAll(".bm-pub").length,
        btnInfo, posts: window.__pbPosts });
    })()`));
    check("两平台发布行渲染", s1.n === 2, JSON.stringify(s1).slice(0, 120));
    check("番茄卡：已发 8 吃平台实况（本地台账 7 + 重试记录 2 条不虚高）+ 校准按钮",
      s1.btnInfo[0].btns.includes("发一章（已发 8）") && s1.btnInfo[0].btns.includes("校准"),
      JSON.stringify(s1.btnInfo[0]));
    check("番茄 tooltip：平台实况 8 + 本地台账 7 + 校准时间",
      s1.btnInfo[0].title.includes("平台实况 8") && s1.btnInfo[0].title.includes("本地台账 7")
      && s1.btnInfo[0].title.includes("校准于"), s1.btnInfo[0].title);
    check("七猫卡：无校准记录显示本地台账口径 已发 1（记录数去重）",
      s1.btnInfo[1].btns.includes("发一章（已发 1）"), JSON.stringify(s1.btnInfo[1]));
    check("七猫 tooltip：本地台账口径提示", s1.btnInfo[1].title.includes("本地台账口径"),
      s1.btnInfo[1].title);
    const syncs = (s1.posts || []).filter(p => p.url.includes("sync-published"));
    check("打开作品页自动校准：只对未校准的七猫触发一次（番茄时间新鲜不重触）",
      syncs.length === 1 && syncs[0].body.includes("qimao") && !syncs[0].body.includes("manual"),
      JSON.stringify(s1.posts));
  } catch (e) {
    check("异常中断", false, String(e && e.stack || e).slice(0, 400));
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
  const fails = results.filter((r) => !r.ok);
  console.log("== %d/%d pass" + (fails.length ? "（FAIL %s）" : ""),
    results.length - fails.length, results.length, fails.map((f) => f.name).join(", "));
  process.exit(fails.length ? 1 : 0);
}

function timeStr() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

main();
