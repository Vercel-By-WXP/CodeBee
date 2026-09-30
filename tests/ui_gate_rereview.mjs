/* 质量门禁弹框三选一核验（建书被闸门拦后的出口）：Edge headless + CDP。
 * 背景闸门拦截原先只有原生 confirm「强制放行」一条路；现在升级为应用内
 * 弹框：取消 / ↻ 重新评审（重写未达标章，走 /api/tasks/<id>/retry）/
 * 强制放行（uiPrompt 收人工复核原因后带 force 三件套重发）。
 * 1) 点「创建作品」→ stub fetch 回 400+quality_gate → 弹框出现，三按钮齐
 * 2) 点「重新评审」→ retry POST 发出、create-book 不带 force 重发、弹框关
 * 3) 再点「创建作品」→「强制放行…」→ uiPrompt 填原因 → create-book 带
 *    force/force_confirmed/force_reason 重发
 * 4) 取消：点「取消」→ 无任何追加 POST
 * 5) 守卫：任务运行中时弹框不出现「重新评审」按钮
 * （fetch stub 只拦发布/重试相关口，其余走真后端；不打真浏览器。） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";

const PORT = 18971;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9399;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

function killTree(pid) {
  return new Promise((res) => {
    try {
      execFile("taskkill", ["/PID", String(pid), "/T", "/F"], () => res());
    } catch (e) { res(); }
  });
}

async function waitPortGone(port, tries = 10) {
  const { execFile: ef } = await import("node:child_process");
  for (let i = 0; i < tries; i++) {
    const out = await new Promise((res) => {
      ef("netstat", ["-ano"], (e, stdout) => res(e ? "" : String(stdout)));
    });
    // 只认 LISTENING：连接关闭后的 TIME_WAIT 条目不算残留
    if (!out.split("\n").some((l) => l.includes(":" + port + " ") && l.includes("LISTENING"))) {
      return true;
    }
    await sleep(500);
  }
  return false;
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "codebee-gate-"));
  const dataDir = join(tmp, "data");
  const wd = join(tmp, "wd");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-gate-1"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  mkdirSync(wd, { recursive: true });
  const now = timeStr();

  // 种子：done 连载任务（verdict 未达标）——闸门数据面由 stub 400 提供，
  // 种 run verdict 只为任务详情渲染真实可信。
  writeFileSync(join(dataDir, "tasks", "gate-t1.json"), JSON.stringify({
    id: "gate-t1", type: "serial_novel", title: "闸门重评回归", goal: "测试",
    context: "", workdir: wd, mode: "auto", difficulty: "auto",
    created_at: now, status: "done",
    serial: { chapters: 4, words_per_chapter: 2000, start_chapter: 1 },
    book_meta: {
      fanqie: { status: "done", at: now, source: "测试",
        data: { book_name: "闸门书", summary: "简介", signing_mode: "连载模式",
          category: "都市", tags_theme: ["重生"] } },
    },
  }), "utf-8");
  writeFileSync(join(wd, "第1章 风起.md"), "# 第1章 风起\n\n" + "正文".repeat(400), "utf-8");
  writeFileSync(join(dataDir, "runs", "r-gate-1", "run.json"), JSON.stringify({
    id: "r-gate-1", kind: "orchestration", title: "闸门重评回归", task_id: "gate-t1",
    entry_id: null, op: null, status: "done",
    steps: [{ n: 1, role: "impl", agent: "claude", agent_label: "Claude Code", note: "",
      status: "done", started_at: "00:00:01", ended_at: null, duration_s: 1, exit_code: 0,
      summary: "完成", log: null, cost_usd: 0, tokens: 0 }],
    created_at: now, started_at: now, ended_at: now, cost_usd: 0, tokens: 0,
    error: "", verdict: { publishable: false, global_pass: true,
      global_reviewer_count: 1, chapter_scores: [], major_issues: [] },
    summary: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "publish", "books.json"), JSON.stringify({
    "gate-t1": {},
  }), "utf-8");

  const pids = [];
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    pids.push(svc.pid);
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    const st0 = await (await fetch(SERVICE + "/api/state")).json();
    check("种子任务在场（done 连载）",
      ((st0.tasks || []).find((x) => x.id === "gate-t1") || {}).status === "done",
      JSON.stringify((st0.tasks || []).map((x) => x.id + ":" + x.status)));

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    pids.push(edge.pid);
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

    // stub fetch（导航后注入）：发布动作回 400+quality_gate；重试/运行详情捕包
    await evalJs(`(window.__pbCreate=[]); (window.__pbRetry=[]); (function(){
      window.alert = () => true; window.confirm = () => true;
      const GATE = { error: "质量门禁拦截：最近一次质量评审未达标；有效全局评审不足 2 名",
        quality_gate: { status: "blocked",
          message: "质量门禁拦截：最近一次质量评审未达标；有效全局评审不足 2 名" } };
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        const method = String((opts || {}).method || "GET").toUpperCase();
        if (u.includes("/api/publish/task/gate-t1/create-book") && method === "POST") {
          window.__pbCreate.push({ url: u, body: (opts.body || ""), ts: Date.now() });
          return new Response(JSON.stringify(GATE), { status: 400 });
        }
        if (u.includes("/api/tasks/gate-t1/retry") && method === "POST") {
          window.__pbRetry.push({ url: u, body: (opts.body || ""), ts: Date.now() });
          return new Response(JSON.stringify({ run_id: "r-gate-new" }), { status: 200 });
        }
        if (u.includes("/api/runs/r-gate-new")) {
          return new Response(JSON.stringify({ id: "r-gate-new", status: "queued",
            task_id: "gate-t1", title: "重评", steps: [], created_at: "2026-09-30 00:00:00",
            cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "" }), { status: 200 });
        }
        if (u === "/api/state" && window.__forceRunning) {
          const r = await orig(url, opts);
          const j = await r.json();
          (j.tasks || []).forEach((t) => { if (t.id === "gate-t1") t.status = "running"; });
          return new Response(JSON.stringify(j), { status: 200,
            headers: { "Content-Type": "application/json" } });
        }
        if (u === "/api/publish" || u.startsWith("/api/publish?")) {
          return new Response(JSON.stringify({
            platforms: {
              fanqie: { label: "番茄", status: "connected", at: "", error: "", last_action: "" },
              qimao: { label: "七猫", status: "none", at: "", error: "", last_action: "" },
            }, browser_found: true, history: [],
          }), { status: 200 });
        }
        if (u.includes("/api/publish/task/gate-t1/history")) {
          return new Response(JSON.stringify({ history: [], books: {} }), { status: 200 });
        }
        return orig(url, opts);
      };
    })(); true`);

    const askInfo = `(() => {
      const ask = document.getElementById("ask");
      const extra = [...ask.querySelectorAll(".ask-extra")].map(b => b.textContent.trim());
      return JSON.stringify({ open: !ask.classList.contains("hidden"),
        title: (document.getElementById("ask-title") || {}).textContent || "",
        body: (document.getElementById("ask-body") || {}).textContent || "",
        yes: (document.getElementById("ask-yes") || {}).textContent || "",
        extra });
    })()`;

    const clickCreate = `((async () => {
      // 重评分支会 jumpToRun 跳运行详情：每次点击前重开任务详情等发布行渲染
      if (typeof sideOpenTask === "function") sideOpenTask("gate-t1");
      for (let i = 0; i < 16; i++) {
        const btn = [...document.querySelectorAll(".bm-pub")]
          .flatMap((r) => [...r.querySelectorAll("button")])
          .find((b) => b.textContent.includes("创建作品"));
        if (btn) break;
        await new Promise((r2) => setTimeout(r2, 500));
      }
      const btn = [...document.querySelectorAll(".bm-pub")]
        .flatMap((r) => [...r.querySelectorAll("button")])
        .find((b) => b.textContent.includes("创建作品"));
      if (!btn) return "no-btn";
      btn.click();
      // 预检确认也是应用内弹框：点「开始建书」穿过，等真正的闸门弹框到位
      for (let i = 0; i < 20; i++) {
        await new Promise((r2) => setTimeout(r2, 400));
        const ask = document.getElementById("ask");
        if (ask.classList.contains("hidden")) continue;
        const yes = document.getElementById("ask-yes");
        const yesTxt = yes ? yes.textContent : "";
        if (ask.querySelector(".ask-extra") || yesTxt.indexOf("强制放行") >= 0) return "ok";
        if (yesTxt.indexOf("开始建书") >= 0) yes.click();
      }
      return "timeout";
    })())`;
    const waitAsk = async (wantOpen) => {
      for (let i = 0; i < 12; i++) {
        const s = JSON.parse(await evalJs(askInfo));
        if (s.open === wantOpen) return s;
        await sleep(400);
      }
      return JSON.parse(await evalJs(askInfo));
    };

    /* 1) 点「创建作品」→ 闸门弹框出现，三按钮齐 */
    check("qimao 卡有「创建作品」按钮",
      (await evalJs(clickCreate)) === "ok");
    const g1 = await waitAsk(true);
    check("闸门弹框出现且标题正确", g1.open && g1.title === "质量门禁未通过", JSON.stringify(g1));
    check("弹框文案带拦截原因",
      g1.body.includes("最近一次质量评审未达标") && g1.body.includes("有效全局评审不足"),
      g1.body.slice(0, 200));
    check("三按钮齐：重新评审 / 取消 / 强制放行…",
      g1.extra.includes("↻ 重新评审") && g1.yes === "强制放行…",
      JSON.stringify({ extra: g1.extra, yes: g1.yes }));

    /* 2) 点「重新评审」→ retry POST、无 force 重发 */
    await evalJs(`(() => {
      const b = document.querySelector("#ask .ask-extra");
      b.click(); return "ok";
    })()`);
    await waitAsk(false);
    const g2 = JSON.parse(await evalJs(`JSON.stringify({ create: window.__pbCreate, retry: window.__pbRetry })`));
    check("重评 retry POST 已发出", g2.retry.length === 1 &&
      g2.retry[0].url.includes("/api/tasks/gate-t1/retry"), JSON.stringify(g2.retry));
    check("create-book 未带 force 重发",
      g2.create.length === 1 && !(g2.create[0].body || "").includes("force"),
      JSON.stringify(g2.create));
    check("弹框已关（走重评后不继续发布）", !(await waitAsk(false)).open);

    /* 3) 强制放行：uiPrompt 收原因 → force 三件套重发 */
    await evalJs(clickCreate);
    const g3 = await waitAsk(true);
    check("再次拦截弹框（第二次）", g3.open, JSON.stringify(g3));
    await evalJs(`(() => { document.getElementById("ask-yes").click(); return "ok"; })()`);
    let hasInput = false;
    for (let i = 0; i < 12 && !hasInput; i++) {
      hasInput = await evalJs(`(() => {
        const inp = document.getElementById("ask-input");
        return !!inp && !document.getElementById("ask").classList.contains("hidden");
      })()`);
      if (!hasInput) await sleep(400);
    }
    check("强制放行弹出人工复核原因输入框", !!hasInput);
    const t0 = await evalJs(`(() => {
      const inp = document.getElementById("ask-input");
      inp.value = "已人工复核稿件与平台版本";
      document.getElementById("ask-yes").click();
      return Date.now();
    })()`);
    // 等待式断言：force 重发到达即读（不赌固定延迟）
    let fpost = null, elapsed = 0;
    for (let i = 0; i < 20 && !fpost; i++) {
      await sleep(400);
      elapsed = Date.now() - t0;
      const g4 = JSON.parse(await evalJs(`JSON.stringify(window.__pbCreate)`));
      fpost = g4.map((p) => JSON.parse(p.body || "{}")).find((b) => b.force === true) || null;
    }
    check("create-book 带 force 三件套重发",
      !!fpost && fpost.force === true && fpost.force_confirmed === true &&
      fpost.force_reason === "已人工复核稿件与平台版本" && fpost.platform === "fanqie",
      JSON.stringify(fpost || {}).slice(0, 240) + " elapsed=" + elapsed + "ms");

    /* 4) 取消：点击后计数冻结（无追加 POST） */
    await evalJs(clickCreate);
    await waitAsk(true);
    const n0 = JSON.parse(await evalJs(
      `JSON.stringify({ c: window.__pbCreate.length, r: window.__pbRetry.length })`));
    await evalJs(`(() => { document.getElementById("ask-no").click(); return "ok"; })()`);
    await sleep(1500);
    const n1 = JSON.parse(await evalJs(
      `JSON.stringify({ c: window.__pbCreate.length, r: window.__pbRetry.length })`));
    check("取消后计数冻结（retry 仍 1 条、无 force 追加）",
      n1.c === n0.c && n1.r === 1, JSON.stringify({ before: n0, after: n1 }));

    /* 5) 守卫：任务运行中 → 不给「重新评审」按钮 */
    await evalJs(`(() => {
      window.__forceRunning = true;
      const tk = ((S.state || {}).tasks || []).find(x => x.id === "gate-t1");
      if (tk) tk.status = "running";
      if (typeof poll === "function") poll();
      return "ok";
    })()`);
    await sleep(600);
    await evalJs(clickCreate);
    const g6 = await waitAsk(true);
    check("运行中弹框不出现「重新评审」按钮",
      g6.open && g6.extra.length === 0, JSON.stringify({ extra: g6.extra, open: g6.open }));
  } finally {
    try { ws && ws.close(); } catch (e) { }
    for (const pid of pids) await killTree(pid);
    await sleep(800);
    const gone = await waitPortGone(PORT);
    console.log(gone ? "  ✓ 端口已释放 " + PORT : "  ✗ 端口未释放 " + PORT + "（残留进程需按 PID 清）");
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
