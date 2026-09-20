/* 一键发布面板核验（bookmeta 分区内发布行）：Edge headless + CDP。
 * 1) 连载任务详情页 bookmeta 分区：两平台卡各带发布行（.bm-pub）；
 * 2) 状态徽章吃 /api/publish：fanqie=已连接（stub）、qimao=未连接；
 * 3) 已建书平台显示「发一章（已发 N）」，未建书显示「创建作品」；
 * 4) 点「发一章」→ dir/scan 列章稿（.md 且过滤「作品信息-*.md」）；
 * 5) 点章稿 → confirm 自动通过 → POST /api/publish/task/<id>/chapter
 *    body={platform, file} 被正确发出（stub fetch 捕获，不打真浏览器）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18921;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9361;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "codebee-pubpanel-"));
  const dataDir = join(tmp, "data");
  const wd = join(tmp, "wd");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-pub-1"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  mkdirSync(wd, { recursive: true });
  const now = timeStr();

  // 种子：done 连载任务（bookmeta 已生成）+ workdir 章稿 + 一条 done run + 已登记作品
  writeFileSync(join(dataDir, "tasks", "pub-t1.json"), JSON.stringify({
    id: "pub-t1", type: "serial_novel", title: "发布面板回归", goal: "测试",
    context: "", workdir: wd, mode: "auto", difficulty: "auto",
    created_at: now, status: "done",
    serial: { chapters: 5, words_per_chapter: 2000, start_chapter: 1 },
    book_meta: {
      fanqie: { status: "done", at: now, source: "测试",
        data: { book_name: "测试书", summary: "简介", signing_mode: "连载模式",
          category: "都市", tags_theme: ["重生"] } },
    },
  }), "utf-8");
  writeFileSync(join(wd, "第1章 风起.md"), "# 第1章 风起\n\n" + "正文".repeat(400), "utf-8");
  writeFileSync(join(wd, "第2章 云涌.md"), "# 第2章 云涌\n\n" + "正文".repeat(400), "utf-8");
  writeFileSync(join(wd, "作品信息-番茄.md"), "# 作品信息（番茄）\n归档文件不应出现在章稿列表", "utf-8");
  writeFileSync(join(dataDir, "runs", "r-pub-1", "run.json"), JSON.stringify({
    id: "r-pub-1", kind: "orchestration", title: "发布面板回归", task_id: "pub-t1",
    entry_id: null, op: null, status: "done",
    steps: [{ n: 1, role: "impl", agent: "claude", agent_label: "Claude Code", note: "",
      status: "done", started_at: "00:00:01", ended_at: null, duration_s: 1, exit_code: 0,
      summary: "完成", log: null, cost_usd: 0, tokens: 0 }],
    created_at: now, started_at: now, ended_at: now, cost_usd: 0, tokens: 0,
    error: "", verdict: null, summary: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "publish", "books.json"), JSON.stringify({
    "pub-t1": { fanqie: { book_id: "", title: "测试书", url: "", created_at: now } },
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
    // 指纹自检：打到的必须是我们的服务（防端口双绑打别人数据）
    const pv = await (await fetch(SERVICE + "/api/publish")).json();
    check("/api/publish 指纹（番茄/七猫 + 台账空）",
      pv.platforms && pv.platforms.fanqie && pv.platforms.fanqie.label === "番茄"
      && Array.isArray(pv.history), JSON.stringify(pv).slice(0, 160));

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

    // stub fetch + confirm/alert 替换（必须导航后注入：导航前的 about:blank
    // 上下文随导航销毁，原生 confirm 无人应答会挂死 evaluate）
    await evalJs(`(window.__pbPosts=[]); (function(){
      window.alert = () => true; window.confirm = () => true;
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/api/publish/task/") && u.includes("/history")) {
          return new Response(JSON.stringify({
            history: [{ platform: "fanqie", action: "upload_chapter", chapter_no: 1,
              title: "第1章 风起", ok: true, ts: "2026-09-18 10:00:00" }],
            books: { fanqie: { book_id: "", title: "测试书", url: "", created_at: "" } },
          }), { status: 200 });
        }
        if (u === "/api/publish" || u.startsWith("/api/publish?")) {
          return new Response(JSON.stringify({
            platforms: {
              fanqie: { label: "番茄", status: "connected", at: "", error: "", last_action: "" },
              qimao: { label: "七猫", status: "none", at: "", error: "", last_action: "" },
            }, browser_found: true, history: [],
          }), { status: 200 });
        }
        if (u.startsWith("/api/dir/scan")) {
          return new Response(JSON.stringify({ files: [
            { name: "第1章 风起.md" }, { name: "第2章 云涌.md" }, { name: "作品信息-番茄.md" },
          ] }), { status: 200 });
        }
        if (u.includes("/api/publish/") && (opts || {}).method === "POST") {
          window.__pbPosts.push({ url: u, body: (opts.body || "") });
          return new Response(JSON.stringify({ ok: true, started: true }), { status: 200 });
        }
        return orig(url, opts);
      };
    })(); true`);

    /* 1-3) 打开详情页（bookmeta 分区）→ 发布行渲染 */
    const s1 = JSON.parse(await evalJs(`(async () => {
      sideOpenTask("pub-t1");
      for (let i = 0; i < 14; i++) {
        await new Promise(r2 => setTimeout(r2, 500));
        if (document.querySelectorAll(".bm-pub").length >= 2) break;
      }
      await new Promise(r2 => setTimeout(r2, 600));
      const box = document.getElementById("rd-bookmeta");
      const tk = (((S.state || {}).tasks || []).find(x => x.id === "pub-t1")) || {};
      return JSON.stringify({
        diag: {
          detailKey: S.detailTaskKey || "", boxExists: !!box,
          boxHidden: box ? box.classList.contains("hidden") : null,
          boxLen: box ? box.innerHTML.length : 0,
          hasSerial: !!tk.serial, hasBookMeta: !!tk.book_meta, type: tk.type,
        },
        n: document.querySelectorAll(".bm-pub").length,
        badges: [...document.querySelectorAll(".bm-pub .pb-badge")].map(x => x.textContent.trim()),
        btns: [...document.querySelectorAll(".bm-pub")].map(x => [...x.querySelectorAll("button")].map(b => b.textContent.trim()).join("|")),
        fqHasBook: !!document.querySelector(".bm-pub .pb-book"),
      });
    })()`));
    check("诊断：bookmeta 分区可见且任务带 serial/book_meta",
      s1.diag.boxExists && s1.diag.hasSerial && s1.diag.hasBookMeta, JSON.stringify(s1.diag));
    check("两平台卡各带发布行", s1.n === 2, JSON.stringify(s1));
    check("徽章：番茄已连接 / 七猫未连接",
      s1.badges[0] === "已连接" && s1.badges[1] === "未连接", JSON.stringify(s1));
    check("已建书平台有「发一章（已发 1）」+ 已建书标记",
      s1.btns[0].includes("发一章（已发 1）") && s1.fqHasBook, JSON.stringify(s1));
    check("未建书平台显示「创建作品」", s1.btns[1].includes("创建作品"), JSON.stringify(s1));

    /* 4) 点「发一章」→ 章稿列表（过滤作品信息归档） */
    const s2 = JSON.parse(await evalJs(`(async () => {
      const rows = [...document.querySelectorAll(".bm-pub")];
      const btn = [...rows[0].querySelectorAll("button")].find(b => b.textContent.includes("发一章"));
      btn.click();
      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 300));
        if (document.querySelectorAll("#pb-ch-fanqie .pb-ch-item").length) break;
      }
      const items = [...document.querySelectorAll("#pb-ch-fanqie .pb-ch-item")].map(b => b.textContent.trim());
      return JSON.stringify({ items });
    })()`));
    check("章稿列表两章且过滤归档文件",
      s2.items.length === 2 && s2.items.includes("第1章 风起.md") && !s2.items.some(x => x.includes("作品信息")),
      JSON.stringify(s2));

    /* 5) 点章稿 → confirm 自动过 → POST 发出且 body 正确 */
    const s3 = JSON.parse(await evalJs(`(async () => {
      const btn = [...document.querySelectorAll("#pb-ch-fanqie .pb-ch-item")]
        .find(b => b.textContent.includes("第1章"));
      btn.click();
      await new Promise(r => setTimeout(r, 700));
      return JSON.stringify({ posts: window.__pbPosts });
    })()`));
    const post = (s3.posts || []).find(p => p.url.includes("/chapter"));
    check("发章 POST 已发出", !!post, JSON.stringify(s3));
    if (post) {
      const body = JSON.parse(post.body || "{}");
      check("POST body：platform=fanqie file=第1章 风起.md",
        body.platform === "fanqie" && body.file === "第1章 风起.md", post.body);
    }

    /* 6) 登记已有作品：未建书卡（qimao）出现入口 → 表单开合 → POST body 正确
     * → 真实后端落 books.json（Node 侧直打，绕开页面 stub）。 */
    const s4 = JSON.parse(await evalJs(`(async () => {
      const rows = [...document.querySelectorAll(".bm-pub")];
      const reg = [...rows[1].querySelectorAll("button")].find(b => b.textContent.includes("登记已有作品"));
      if (!reg) return JSON.stringify({ err: "no-register-btn", html: rows[1].innerHTML.slice(0, 300) });
      const form = document.getElementById("pb-reg-qimao");
      const hiddenBefore = form.classList.contains("hidden");
      reg.click();
      const shownAfter = !form.classList.contains("hidden");
      document.getElementById("pb-reg-title-qimao").value = "同事手建的书";
      document.getElementById("pb-reg-id-qimao").value = "12345";
      reg.click();
      const hiddenAgain = form.classList.contains("hidden");
      reg.click();
      [...rows[1].querySelectorAll("button")].find(b => b.textContent.includes("确认登记")).click();
      await new Promise(r => setTimeout(r, 700));
      return JSON.stringify({ hiddenBefore, shownAfter, hiddenAgain, posts: window.__pbPosts });
    })()`));
    check("未建书卡有「登记已有作品」且内联表单可开合",
      s4.hiddenBefore && s4.shownAfter && s4.hiddenAgain, JSON.stringify(s4));
    const rpost = (s4.posts || []).find(p => p.url.includes("/register-book"));
    check("登记 POST 已发出（页面通道）", !!rpost, JSON.stringify(s4.posts));
    if (rpost) {
      const body = JSON.parse(rpost.body || "{}");
      check("登记 POST body：platform=qimao title/book_id 正确",
        body.platform === "qimao" && body.title === "同事手建的书" && body.book_id === "12345", rpost.body);
    }
    const real1 = await (await fetch(SERVICE + "/api/publish/task/pub-t1/register-book", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: "qimao", title: "真机登记书", book_id: "888" }),
    })).json();
    const real2 = await (await fetch(SERVICE + "/api/publish/task/pub-t1/register-book", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: "qimao", title: "   " }),
    })).json();
    check("真实后端：登记成功 / 空书名 400 人话报错",
      real1.ok === true && (real2.error || "").includes("作品名"), JSON.stringify({ real1, real2 }));
    const booksOnDisk = JSON.parse(readFileSync(join(dataDir, "publish", "books.json"), "utf-8"));
    check("books.json 已登记（发一章闸门数据源就位）",
      ((booksOnDisk["pub-t1"] || {}).qimao || {}).title === "真机登记书"
      && booksOnDisk["pub-t1"].qimao.book_id === "888", JSON.stringify(booksOnDisk).slice(0, 200));
  } finally {
    try { ws && ws.close(); } catch (e) { }
    try { edge && edge.kill(); } catch (e) { }
    try { svc && svc.kill(); } catch (e) { }
    await sleep(600);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { }
  }
  const bad = results.filter(r => !r.ok).length;
  console.log(bad ? `FAILED ${bad}/${results.length}` : `ALL PASS ${results.length}`);
  process.exit(bad ? 1 : 0);
}

function timeStr() {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

main().catch((e) => { console.error(e); process.exit(1); });
