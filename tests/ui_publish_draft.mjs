/* 全部发草稿（2026-10-09）发布台 UI 回归：Edge headless + CDP。
 * 1) 质量闸拦截（guard_ok=false）但硬护栏过（draft_ok=true）时：
 *    「全部存草稿（N）」「存草稿所选（N）」可点，拦截文案降级为 pb-hint（可先存草稿）；
 * 2) drafted>0 → 「已存草稿 N 章」提示；
 * 3) 点「全部存草稿」→ publish-all body 带 as_draft:true（无 chapters）；
 * 4) 点「存草稿所选」→ body 带 chapters 升序 + as_draft:true；
 * 5) 硬护栏挂（draft_ok=false）→ 草稿按钮禁用 + pb-err 硬原因；
 * 6) 卷头＝整卷开关：取消整卷→该卷退出所选并显示 0/4，勾回恢复全选。
 * 前置 stub：/pending 返回 auto.status 新形状（draft_ok/quality_blockers/drafted）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18939;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9393;
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
  volume: i < 4 ? "第一卷 山门" : "第二卷 出山",
}));

const PENDING_STUB = (over = {}) => JSON.stringify({
  books: [{
    platform: "fanqie", bound: true, title: "测试书",
    pending: ITEMS.length,
    items: ITEMS,
    guard_ok: false, guard_reason: "质量门禁拦截：测试",
    draft_ok: true, draft_reason: "",
    quality_blockers: ["最近一次质量评审未达标", "存在 major 问题（共 8 条）"],
    drafted: 3,
    calibrated: true,
    ...over,
  }],
  running: null,
});

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "codebee-draft-"));
  const dataDir = join(tmp, "data");
  const wd = join(tmp, "wd");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-draft-1"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  mkdirSync(wd, { recursive: true });
  const now = timeStr();

  writeFileSync(join(dataDir, "tasks", "draft-t1.json"), JSON.stringify({
    id: "draft-t1", type: "serial_novel", title: "发草稿回归", goal: "测试",
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
  writeFileSync(join(dataDir, "runs", "r-draft-1", "run.json"), JSON.stringify({
    id: "r-draft-1", kind: "orchestration", title: "发草稿回归", task_id: "draft-t1",
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

    // stub：/pending 给新形状（guard_ok=false 质量闸拦 + draft_ok=true 硬护栏过）
    await evalJs(`(window.__pbPosts=[]); (function(){
      window.alert = () => true; window.confirm = () => true;
      window.prompt = () => "测试复核原因";
      const nowStr = new Date().toLocaleString("sv-SE").replace("T", " ");
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/api/publish/task/") && u.includes("/pending")) {
          return new Response(${JSON.stringify(PENDING_STUB())}, { status: 200 });
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
      sideOpenTask("draft-t1");
      for (let i = 0; i < 14; i++) {
        await new Promise(r2 => setTimeout(r2, 500));
        if (document.querySelectorAll(".pb-pend").length >= 1) break;
      }
      await new Promise(r2 => setTimeout(r2, 500));
      const card = document.querySelector(".bm-pub");
      const byText = (t) => [...card.querySelectorAll("button")].find(b => b.textContent.includes(t)) || null;
      const draftAll = byText("全部存草稿");
      const draftSel = byText("存草稿所选");
      const hints = [...card.querySelectorAll(".pb-hint")].map(h => h.textContent).join("｜");
      const errs = [...card.querySelectorAll(".pb-err")].map(h => h.textContent).join("｜");
      return JSON.stringify({
        draftAll: draftAll ? draftAll.textContent.trim() : null,
        draftAllDisabled: draftAll ? draftAll.disabled : null,
        draftSel: draftSel ? draftSel.textContent.trim() : null,
        draftSelDisabled: draftSel ? draftSel.disabled : null,
        hintHasQuality: hints.includes("质量闸未过"),
        hintHasDraftPath: hints.includes("全部存草稿"),
        hintHasDrafted: hints.includes("已存草稿 3 章"),
        errs, hints: hints.slice(0, 160) });
    })()`));
    check("全部存草稿（8）可点（质量闸拦不禁草稿）",
      (open.draftAll || "").includes("（8）") && open.draftAllDisabled === false, JSON.stringify(open));
    check("存草稿所选（8）可点",
      (open.draftSel || "").includes("（8）") && open.draftSelDisabled === false, JSON.stringify(open));
    check("质量闸拦截降级为 pb-hint 且指出草稿通路",
      open.hintHasQuality && open.hintHasDraftPath && !open.errs, JSON.stringify(open));
    check("已存草稿 3 章提示在场", open.hintHasDrafted, JSON.stringify(open));

    // 点「全部存草稿」→ 确认 → publish-all body 带 as_draft:true（无 chapters）
    const postAll = JSON.parse(await evalJs(`(async () => {
      const card = document.querySelector(".bm-pub");
      const btn = [...card.querySelectorAll("button")].find(b => b.textContent.includes("全部存草稿"));
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
      return JSON.stringify(p ? p.body : { none: true, posts: window.__pbPosts });
    })()`));
    check("全部存草稿 → publish-all as_draft:true、不带 chapters",
      postAll.as_draft === true && postAll.platform === "fanqie"
      && postAll.chapters === undefined, JSON.stringify(postAll));

    // 点「存草稿所选」→ body 带 chapters 升序 + as_draft:true
    const postSel = JSON.parse(await evalJs(`(async () => {
      const box = document.querySelector(".pb-pend");
      const btn = [...box.querySelectorAll("button")].find(b => b.textContent.includes("存草稿所选"));
      btn.click();
      for (let i = 0; i < 10; i++) {
        await new Promise(r2 => setTimeout(r2, 300));
        const yes = document.querySelector("#ask-yes");
        if (yes) { yes.click(); break; }
      }
      for (let i = 0; i < 10; i++) {
        await new Promise(r2 => setTimeout(r2, 400));
        if (window.__pbPosts.some(p => p.body && p.body.as_draft && p.body.chapters)) break;
      }
      const p = window.__pbPosts.find(p => p.body && p.body.as_draft && p.body.chapters);
      return JSON.stringify(p ? p.body : { none: true, posts: window.__pbPosts });
    })()`));
    check("存草稿所选 → chapters=31..38 升序 + as_draft:true",
      JSON.stringify(postSel.chapters) === JSON.stringify(ITEMS.map(i => i.chapter_no))
      && postSel.as_draft === true, JSON.stringify(postSel));

    // 按卷勾选：卷头＝整卷开关（全选打勾；部分选中显示 已选/总）
    const vol = JSON.parse(await evalJs(`(async () => {
      // 重绘会整块替换 .bm-pub——每次都现查活节点，别捕获旧卡片
      const selBtn = () => { const c = document.querySelector(".bm-pub"); return ([...(c ? c.querySelectorAll("button") : [])].find(b => b.textContent.includes("存草稿所选")) || { textContent: "" }); };
      const chipsOn = () => { const c = document.querySelector(".bm-pub"); return c ? c.querySelectorAll(".pb-chip.on").length : 0; };
      window.__volCalls = [];
      const origVol = window.pbSelVolume;
      window.pbSelVolume = function (t, p, gi, on) {
        const key = t + ":" + p;
        const ent = (S.pbSel || {})[key];
        const g = ent && ent.groups && ent.groups[gi];
        const pre = { size: ent ? ent.sel.size : null, hasGroups: !!(ent && ent.groups), nsLen: g ? g.ns.length : null };
        const r = origVol(t, p, gi, on);
        const ent2 = (S.pbSel || {})[key];
        window.__volCalls.push({ gi, on, pre, post: { size: ent2 ? ent2.sel.size : null, sameEnt: ent2 === ent } });
        return r;
      };
      const before = selBtn().textContent.trim();
      let cb = document.querySelector(".pb-vol-sel input");
      const cb1State = cb ? cb.checked : null;
      if (cb) cb.click();
      for (let i = 0; i < 8; i++) {
        await new Promise(r2 => setTimeout(r2, 250));
        if (selBtn().textContent.includes("（4）")) break;
      }
      const vh = document.querySelector(".pb-vol-sel");
      const off = { header: vh ? vh.textContent.trim() : null, btn: selBtn().textContent.trim(), on: chipsOn() };
      await new Promise(r2 => setTimeout(r2, 600));   // 等重绘彻底稳定再取节点
      cb = document.querySelector(".pb-vol-sel input");
      const cb2State = cb ? { checked: cb.checked, connected: cb.isConnected } : null;
      if (cb) cb.click();
      // 卷头勾选态只有整块重绘才回写（pbSelCountSync 就地同步不碰卷头），
      // 而 pbKick 重绘走 5s 节流——等 checked 翻真再断言，别读在就地同步上
      for (let i = 0; i < 20; i++) {
        await new Promise(r2 => setTimeout(r2, 400));
        const c2 = document.querySelector(".pb-vol-sel input");
        if (c2 && c2.checked && selBtn().textContent.includes("（8）")) break;
      }
      const out = JSON.stringify({ before, cb1State, off, cb2State, restored: selBtn().textContent.trim(),
        stSize: Object.keys(S.pbSel).map(k => k + "=" + S.pbSel[k].sel.size + (S.pbSel[k].groups ? "+g" : "-g")).join(","),
        cardBtns: [...(document.querySelector(".bm-pub") || { querySelectorAll: () => [] }).querySelectorAll(".pb-pend button")].map(b => b.textContent.trim()).join("|") });
      window.pbSelVolume = origVol;
      return out;
    })()`));
    check("卷头渲染为整卷开关（默认全选 8）",
      vol.before.includes("（8）"), JSON.stringify(vol));
    check("取消整卷→该卷 4 章退出所选且卷头显示 0/4",
      vol.off.btn.includes("（4）") && vol.off.on === 4
      && (vol.off.header || "").includes("0/4"), JSON.stringify(vol.off));
    check("勾回整卷→恢复全选", vol.restored.includes("（8）"),
      JSON.stringify({ restored: vol.restored, st: vol.stSize, btns: vol.cardBtns }));

    // 存草稿拒起（另一批自动发布在跑）→ toast 带人话原因（后端互斥，2026-10-10）
    await evalJs(`(function(){
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/publish-all")) {
          window.__pbPosts.push({ url: u, body: JSON.parse((opts || {}).body || "{}") });
          return new Response(JSON.stringify({ error: "检测到另一批自动发布/存草稿进行中（任务 t-other-9）——两边共用平台浏览器会互踩，请等它跑完再存草稿" }), { status: 400 });
        }
        return orig(url, opts);
      };
    })(); true`);
    await evalJs(`(async () => {
      const card = document.querySelector(".bm-pub");
      const btn = [...card.querySelectorAll("button")].find(b => b.textContent.includes("全部存草稿"));
      btn.click();
      for (let i = 0; i < 10; i++) {
        await new Promise(r2 => setTimeout(r2, 300));
        const yes = document.querySelector("#ask-yes");
        if (yes) { yes.click(); break; }
      }
      for (let i = 0; i < 10; i++) {
        await new Promise(r2 => setTimeout(r2, 400));
        if (document.body.innerText.includes("互踩")) break;
      }
    })()`);
    const mutexToast = await evalJs(`document.body.innerText.includes("互踩") && document.body.innerText.includes("存草稿启动失败")`);
    check("另一批在跑→存草稿拒起 toast 带互踩原因", mutexToast === true);

    // 批次过程 notes（对账剔除/重试）在进度区可见
    await evalJs(`(function(){
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/api/publish/task/") && u.includes("/pending")) {
          const d = JSON.parse(${JSON.stringify(PENDING_STUB())});
          d.running = { platform: "fanqie", at: "2026-10-10 16:00:00", done: 1,
            total: 7, status: "running", as_draft: true, error: "",
            notes: ["对账剔除 1 章已发布/已存稿，实存 7 章", "第 32 章连接中断，5 秒后重试一次"] };
          return new Response(JSON.stringify(d), { status: 200 });
        }
        return orig(url, opts);
      };
      S._pbSig = ""; pbKick();
    })(); true`);
    await sleep(2500);
    const notesShown = await evalJs(`(() => {
      const card = document.querySelector(".bm-pub");
      const t = card ? card.innerText : "";
      return { note: t.includes("对账剔除"), retry: t.includes("重试一次") };
    })()`);
    check("批次 notes（对账/重试）在进度区可见",
      notesShown.note && notesShown.retry, JSON.stringify(notesShown));

    // 硬护栏挂（draft_ok=false）：草稿按钮禁用 + pb-err 硬原因（不是质量话术）
    await evalJs(`(function(){
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes("/api/publish/task/") && u.includes("/pending")) {
          return new Response(${JSON.stringify(PENDING_STUB({
            draft_ok: false, draft_reason: "今日已发 10 章（上限 10），为防风控明天再发",
            quality_blockers: [],
          }))}, { status: 200 });
        }
        return orig(url, opts);
      };
      S._pbSig = ""; pbKick();
    })(); true`);
    await sleep(2500);
    const hard = JSON.parse(await evalJs(`(async () => {
      const card = document.querySelector(".bm-pub");
      const byText = (t) => [...card.querySelectorAll("button")].find(b => b.textContent.includes(t)) || null;
      const draftAll = byText("全部存草稿");
      const draftSel = byText("存草稿所选");
      const errs = [...card.querySelectorAll(".pb-err")].map(h => h.textContent).join("｜");
      return JSON.stringify({
        draftAllDisabled: draftAll ? draftAll.disabled : null,
        draftSelDisabled: draftSel ? draftSel.disabled : null,
        errs });
    })()`));
    check("硬护栏挂：草稿按钮禁用 + pb-err 硬原因（每日上限）",
      hard.draftAllDisabled === true && hard.draftSelDisabled === true
      && hard.errs.includes("上限"), JSON.stringify(hard));
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
  console.log(`== ui_publish_draft: ${results.length - bad.length}/${results.length} passed` +
    (bad.length ? ` — FAIL: ${bad.map(b => b.name).join("; ")}` : ""));
  process.exit(bad.length ? 1 : 0);
}

function timeStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

main();
