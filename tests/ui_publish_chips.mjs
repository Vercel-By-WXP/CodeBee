/* 发布台批量选择（pb-pend chips）UI 回归：Edge headless + CDP。
 * 1) 待发清单渲染 chips，默认全选，「发布所选（N）」计数=N；
 * 2) 点掉一个 chip → 计数联动 -1，再勾回 → 计数还原；
 * 3) 清空 → 计数 0 且按钮 disabled；全选 → 计数还原；
 * 4) 点「发布所选」→ POST publish-all body 带 chapters=勾选章号升序数组。
 * 前置 stub：/pending 返回 items（auto.status 形状），/history 给已建书+已发数。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18936;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9379;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

const ITEMS = Array.from({ length: 8 }, (_, i) => ({
  chapter_no: 31 + i, file: `chapter-${31 + i}.md`, size: 3000 + i,
}));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "codebee-chips-"));
  const dataDir = join(tmp, "data");
  const wd = join(tmp, "wd");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-chips-1"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  mkdirSync(wd, { recursive: true });
  const now = timeStr();

  writeFileSync(join(dataDir, "tasks", "chips-t1.json"), JSON.stringify({
    id: "chips-t1", type: "serial_novel", title: "勾选回归", goal: "测试",
    context: "", workdir: wd, mode: "auto", difficulty: "auto",
    created_at: now, status: "done",
    serial: { chapters: 8, words_per_chapter: 2000, start_chapter: 31 },
    book_meta: {
      fanqie: { status: "done", at: now, source: "测试",
        data: { book_name: "测试书", summary: "简介", signing_mode: "连载模式",
          category: "都市", tags_theme: ["重生"] } },
    },
  }), "utf-8");
  for (const it of ITEMS)
    writeFileSync(join(wd, it.file), "# 第" + it.chapter_no + "章 测试\n\n" + "正文".repeat(300), "utf-8");
  writeFileSync(join(dataDir, "runs", "r-chips-1", "run.json"), JSON.stringify({
    id: "r-chips-1", kind: "orchestration", title: "勾选回归", task_id: "chips-t1",
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

    // stub：/pending 给 items（guard_ok=false 也必须有按钮可点——门禁走对话不发死按钮）
    await evalJs(`(window.__pbPosts=[]); (function(){
      window.alert = () => true; window.confirm = () => true;
      window.prompt = () => "测试复核原因";
      const nowStr = new Date().toLocaleString("sv-SE").replace("T", " ");
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/api/publish/task/") && u.includes("/pending")) {
          return new Response(JSON.stringify({ books: [{
            platform: "fanqie", bound: true, title: "测试书",
            pending: ${ITEMS.length},
            items: ${JSON.stringify(ITEMS)},
            guard_ok: false, guard_reason: "质量门禁拦截：测试",
            calibrated: true }], running: null }), { status: 200 });
        }
        if (u.includes("/api/publish/task/") && u.includes("/history")) {
          return new Response(JSON.stringify({
            history: [],
            books: { fanqie: { book_id: "777", title: "测试书", url: "", created_at: "",
              remote_total: 30, remote_published: 30, remote_review: 0,
              remote_synced_at: nowStr } },
            published: { fanqie: 30 },
          }), { status: 200 });
        }
        if (u === "/api/publish" || u.startsWith("/api/publish?")) {
          return new Response(JSON.stringify({
            platforms: {
              fanqie: { label: "番茄", status: "connected", at: "", error: "", last_action: "" },
            }, browser_found: true, history: [],
          }), { status: 200 });
        }
        if (u.includes("/api/publish/") && (opts || {}).method === "POST") {
          window.__pbPosts.push({ url: u, body: JSON.parse((opts || {}).body || "{}") });
          return new Response(JSON.stringify({ ok: true, started: true }), { status: 200 });
        }
        return orig(url, opts);
      };
    })(); true`);

    const open = JSON.parse(await evalJs(`(async () => {
      sideOpenTask("chips-t1");
      for (let i = 0; i < 14; i++) {
        await new Promise(r2 => setTimeout(r2, 500));
        if (document.querySelectorAll(".pb-pend").length >= 1) break;
      }
      await new Promise(r2 => setTimeout(r2, 500));
      const box = document.querySelector(".pb-pend");
      const chips = [...box.querySelectorAll(".pb-chip")];
      const selBtn = [...box.querySelectorAll("button")].find(b => b.textContent.includes("发布所选"));
      return JSON.stringify({
        chips: chips.length,
        checked: chips.filter(c => c.querySelector("input").checked).length,
        selBtn: selBtn ? selBtn.textContent.trim() : null,
        selBtnDisabled: selBtn ? selBtn.disabled : null,
      });
    })()`));
    check("chips 渲染 8 个且默认全选", open.chips === 8 && open.checked === 8, JSON.stringify(open));
    check("发布所选计数=8（guard false 不再禁用）",
      (open.selBtn || "").includes("（8）") && open.selBtnDisabled === false, JSON.stringify(open));

    // 点掉 31：计数联动 7，chip 视觉取消
    const after1 = JSON.parse(await evalJs(`(async () => {
      const box = document.querySelector(".pb-pend");
      const c31 = [...box.querySelectorAll(".pb-chip")]
        .find(c => c.textContent.trim() === "31");
      c31.querySelector("input").click();
      await new Promise(r2 => setTimeout(r2, 1200));
      const box2 = document.querySelector(".pb-pend");
      const chips = [...box2.querySelectorAll(".pb-chip")];
      const selBtn = [...box2.querySelectorAll("button")].find(b => b.textContent.includes("发布所选"));
      return JSON.stringify({
        checked: chips.filter(c => c.querySelector("input").checked).length,
        c31: chips.find(c => c.textContent.trim() === "31")
          ? chips.find(c => c.textContent.trim() === "31").querySelector("input").checked
          : null,
        selBtn: selBtn ? selBtn.textContent.trim() : null });
    })()`));
    check("点掉 31：勾选数 7、计数（7）联动", after1.checked === 7 && (after1.selBtn || "").includes("（7）"),
      JSON.stringify(after1));
    check("chip 31 保持取消态（重绘不弹回）", after1.c31 === false, JSON.stringify(after1));

    // 再勾回：计数还原 8
    const after2 = JSON.parse(await evalJs(`(async () => {
      const box = document.querySelector(".pb-pend");
      const c31 = [...box.querySelectorAll(".pb-chip")].find(c => c.textContent.trim() === "31");
      c31.querySelector("input").click();
      await new Promise(r2 => setTimeout(r2, 1200));
      const box2 = document.querySelector(".pb-pend");
      const chips = [...box2.querySelectorAll(".pb-chip")];
      const selBtn = [...box2.querySelectorAll("button")].find(b => b.textContent.includes("发布所选"));
      return JSON.stringify({ checked: chips.filter(c => c.querySelector("input").checked).length,
        selBtn: selBtn ? selBtn.textContent.trim() : null });
    })()`));
    check("勾回 31：计数还原（8）", after2.checked === 8 && (after2.selBtn || "").includes("（8）"),
      JSON.stringify(after2));

    // 清空 → 0 + disabled；全选 → 还原
    const after3 = JSON.parse(await evalJs(`(async () => {
      const box = document.querySelector(".pb-pend");
      [...box.querySelectorAll("a")].find(a => a.textContent.includes("清空")).click();
      await new Promise(r2 => setTimeout(r2, 1200));
      const box2 = document.querySelector(".pb-pend");
      const chips = [...box2.querySelectorAll(".pb-chip")];
      const selBtn = [...box2.querySelectorAll("button")].find(b => b.textContent.includes("发布所选"));
      const r1 = { checked: chips.filter(c => c.querySelector("input").checked).length,
        selBtn: selBtn ? selBtn.textContent.trim() : null, disabled: selBtn ? selBtn.disabled : null };
      [...box2.querySelectorAll("a")].find(a => a.textContent.includes("全选")).click();
      await new Promise(r2 => setTimeout(r2, 1200));
      const box3 = document.querySelector(".pb-pend");
      const chips3 = [...box3.querySelectorAll(".pb-chip")];
      const selBtn3 = [...box3.querySelectorAll("button")].find(b => b.textContent.includes("发布所选"));
      return JSON.stringify({ cleared: r1, back: {
        checked: chips3.filter(c => c.querySelector("input").checked).length,
        selBtn: selBtn3 ? selBtn3.textContent.trim() : null } });
    })()`));
    check("清空：计数 0 且按钮禁用", after3.cleared.checked === 0
      && (after3.cleared.selBtn || "").includes("（0）") && after3.cleared.disabled === true,
      JSON.stringify(after3.cleared));
    check("全选：计数还原（8）", after3.back.checked === 8
      && (after3.back.selBtn || "").includes("（8）"), JSON.stringify(after3.back));

    // 点「发布所选」→ 确认弹框（uiConfirm 自绘，#ask-yes）→ publish-all body 带 chapters 升序数组
    const post = JSON.parse(await evalJs(`(async () => {
      const box = document.querySelector(".pb-pend");
      const btn = [...box.querySelectorAll("button")].find(b => b.textContent.includes("发布所选"));
      btn.click();
      for (let i = 0; i < 10; i++) {
        await new Promise(r2 => setTimeout(r2, 300));
        const yes = document.querySelector("#ask-yes");
        if (yes) { yes.click(); break; }
      }
      for (let i = 0; i < 10; i++) {
        await new Promise(r2 => setTimeout(r2, 400));
        if (window.__pbPosts.some(p => p.url.includes("publish-all"))) break;
      }
      const p = window.__pbPosts.find(p => p.url.includes("publish-all"));
      return JSON.stringify(p ? { chapters: p.body.chapters, auto_submit: p.body.auto_submit }
                              : { none: true, ask: !!document.querySelector("#ask-yes"),
                                  posts: window.__pbPosts });
    })()`));
    check("发布所选 → publish-all chapters=31..38 升序、人工模式",
      JSON.stringify(post.chapters) === JSON.stringify(ITEMS.map(i => i.chapter_no))
      && post.auto_submit === false, JSON.stringify(post));
  } catch (e) {
    check("异常中断", false, String(e && e.stack || e).slice(0, 400));
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { if (edge) edge.kill(); } catch (e) {}
    try { if (svc) { svc.kill(); } } catch (e) {}
    // Edge 引擎进程树收尾（按用户数据目录匹配，防残留占 CDP 口）
    try {
      const { execSync } = await import("node:child_process");
      execSync(`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*${join(tmp, "p").replace(/\\\\/g, "\\\\")}*' -and $_.Name -eq 'msedge.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"`, { stdio: "ignore" });
    } catch (e) {}
    try {
      rmSync(tmp, { recursive: true, force: true, maxRetries: 3, retryDelay: 500 });
    } catch (e) { /* Edge 句柄释放慢：临时目录留给系统清理 */ }
  }
  const bad = results.filter(r => !r.ok);
  console.log(`== ui_publish_chips: ${results.length - bad.length}/${results.length} passed` +
    (bad.length ? ` — FAIL: ${bad.map(b => b.name).join("; ")}` : ""));
  process.exit(bad.length ? 1 : 0);
}

function timeStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

main();
