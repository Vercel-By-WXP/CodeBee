/* 自动续跑退避窗口回归（2026-09-18 重写任务误判案）：
 * 1) 任务跑过但最新 run 排队中（退避窗口）→ 点任务行必须直开任务详情，
 *    不许再弹「还没跑过」toast——历史轮次的步骤日志就是要给用户看的；
 * 2) 详情头部状态 chip 在退避窗口显示「将于 HH:MM 自动续跑（第 N 次）」，
 *    预定时刻过了翻回「排队中」；
 * 3) 从未跑过的任务依旧 toast 指引，不开空详情。
 * 造数：服务启动收尸会把盘上 queued 收成 failed，所以排队 run 由页面层
 * fetch 桩注入（runs 接口 + state.task_latest），其余全走真实临时服务。
 * 用法：node tests/ui_resume_backoff.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18893;
const CDP_PORT = parseInt(process.env.TUTTI_TEST_CDP || "9393", 10);
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const T_RES = "t-res-20260918-0001";     // 跑过的任务（失败轮 + 页面层注入的排队轮）
const R_DONE = "r-20260918-160000-00r1"; // 失败轮（盘上真实存在）
const R_QUE = "r-20260918-162829-00r2";  // 排队轮（桩注入，带 resume_enqueue_at）
const T_NEVER = "t-never-20260918-0002"; // 从未跑过
const RESUME_AT_FUTURE = "2099-01-01 08:30:00";
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-resume-"));
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", R_DONE), { recursive: true });

  const mkTask = (id, title) => ({
    id, title, type: "novel", serial: { chapters: 8, words_per_chapter: 2000 },
    goal: "造数：" + title, workdir, status: "failed", archived: false,
    created_at: "2026-09-18 15:00:00", mode: "manual",
  });
  writeFileSync(join(dataDir, "tasks", T_RES + ".json"), JSON.stringify(mkTask(T_RES, "退避之书"), null, 2));
  writeFileSync(join(dataDir, "tasks", T_NEVER + ".json"), JSON.stringify(mkTask(T_NEVER, "从没跑过"), null, 2));
  const failedRun = {
    id: R_DONE, kind: "orchestration", title: "退避之书", task_id: T_RES,
    status: "failed", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "mock-a", agent_label: "写手", note: "", status: "done",
        started_at: "16:00:01", ended_at: "16:00:10", duration_s: 9, exit_code: 0,
        summary: "已完成 8 章大纲", log: "steps/01.log", cost_usd: 0, tokens: 0 },
      { n: 2, role: "critique-c2", agent: "mock-b", agent_label: "评审", note: "", status: "failed",
        started_at: "16:00:11", ended_at: "16:00:20", duration_s: 9, exit_code: 1,
        summary: "绑定链全部失效", log: "steps/02.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2026-09-18 16:00:00", started_at: "2026-09-18 16:00:00",
    ended_at: "2026-09-18 16:00:20", cost_usd: 0, tokens: 0,
    error: "第 2 章评审全部失败", auto_resumes: 1,
  };
  writeFileSync(join(dataDir, "runs", R_DONE, "run.json"), JSON.stringify(failedRun, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  // Windows 下 child.kill() 杀不死这套服务（实测僵尸占口，SO_REUSEADDR 双绑
  // 让后续轮次打到空数据僵尸上全线假红）：清理一律 taskkill /F /T 按 PID 杀树。
  const killTree = (p) => {
    try { if (p && p.pid) spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)],
      { stdio: "ignore" }); } catch (e) { /* 尽力而为 */ }
  };
  // 起跑前端口必须干净：宁可失败也不跟僵尸服务对暗号
  let portDirty = false;
  try {
    const listeners = await new Promise((resolve) => {
      const nls = spawn("netstat", ["-ano"], { stdio: ["ignore", "pipe", "ignore"] });
      let buf = "";
      nls.stdout.on("data", (d) => { buf += d; });
      nls.stdout.on("end", () => resolve(buf));
      nls.on("error", () => resolve(""));
    });
    portDirty = listeners.split("\n").some((l) =>
      l.includes(":" + SERVICE_PORT) && l.includes("LISTENING"));
  } catch (e) { /* 查不到就放行，后面的服务端自证会兜底 */ }
  if (portDirty) {
    console.error("端口 " + SERVICE_PORT + " 已被占用（疑似僵尸服务），先清理再跑本测试");
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    process.exit(2);
  }
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    // 启动清扫（Get-CimInstance 扫孤儿 CLI）在重负载机器上可拖到 60s：
    // 等待窗口给足 120s，别把慢启动误判成起不来
    for (let i = 0; i < 240 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); await r.text(); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18893)", up);

    // 服务端自证：造数任务必须已在 /api/state 里（否则是种子/TUTTI_DATA 问题）
    let seedState = null;
    try {
      const sr = await fetch(`${SERVICE}/api/state`);
      const sj = await sr.json();
      seedState = { tasks: (sj.tasks || []).map((t) => t.id), latest: Object.keys(sj.task_latest || {}) };
    } catch (e) { seedState = { err: String(e) }; }
    check("服务端读到两个种子任务", (seedState.tasks || []).includes(T_RES)
      && (seedState.tasks || []).includes(T_NEVER), JSON.stringify(seedState));

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP(9393)", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1400, height: 950, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    // 页面层注入：SSE 关掉防覆盖；applyState 包装把 t-res 的 task_latest 钉成
    // queued + resume_enqueue_at；runs 接口返回「失败轮 + 排队轮」。
    // RESUME_AT 可在测试中途改成过去时刻，验证 chip 翻回「排队中」。
    const inject = await evalJson(`(async () => {
      if (typeof S === "undefined" || typeof applyState !== "function") return { ok: false };
      if (S.es) { try { S.es.close(); } catch (e) {} S.es = null; S.sseLive = false; }
      window.__resumeAt = "${RESUME_AT_FUTURE}";
      const queuedRun = {
        id: "${R_QUE}", kind: "orchestration", title: "退避之书", task_id: "${T_RES}",
        status: "queued", messages: [], steps: [],
        created_at: "2026-09-18 16:28:29", started_at: "", ended_at: "",
        cost_usd: 0, tokens: 0, error: "", auto_resumes: 2,
        resume_enqueue_at: "${RESUME_AT_FUTURE}",
      };
      window.__queuedRun = queuedRun;   // 第 3 步改期要用：翻回过去时刻验证 chip
      const failedRun = ${JSON.stringify(failedRun)};
      const origFetch = window.fetch.bind(window);
      window.fetch = async (u, o) => {
        const url = String(u);
        if (url.includes("/api/tasks/${T_RES}/runs")) {
          return new Response(JSON.stringify({ runs: [queuedRun, failedRun] }),
            { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return origFetch(u, o);
      };
      const origApply = applyState;
      applyState = function (d) {
        if (d && d.task_latest) d.task_latest["${T_RES}"] = queuedRun;
        return origApply(d);
      };
      // 立刻用真实 /api/state 过一遍包装，保证 task_latest 注入生效
      try {
        const r = await origFetch("/api/state");
        applyState(await r.json());
      } catch (e) { return { ok: false, err: String(e) }; }
      return { ok: true, latest: (S.state.task_latest || {})["${T_RES}"].status };
    })()`);
    check("页面层注入生效(排队轮入 task_latest)", inject && inject.ok && inject.latest === "queued",
      JSON.stringify(inject));

    // 等侧栏行渲染（首帧 /api/state + render 的时序在重负载下不稳）：
    const waitForRows = async () => {
      let last = null;
      for (let i = 0; i < 30; i++) {
        last = await evalJson(`({
          n: document.querySelectorAll('.stask').length,
          ids: Array.from(document.querySelectorAll('.stask')).map((e) => e.dataset.task),
          tasks: ((S.state || {}).tasks || []).map((t) => t.id + ':' + t.status),
          sideLen: (document.getElementById('side-tasks') || { innerHTML: '' }).innerHTML.length,
        })`);
        if (last && last.n >= 2) return last;
        await sleep(500);
      }
      return last;
    };
    const rows = await waitForRows();
    check("侧栏两个任务行就绪", !!rows && rows.n >= 2, JSON.stringify(rows));

    // 1) 从未跑过的任务：依旧 toast，不开详情
    const neverRes = await evalJson(`(async () => {
      const row = document.querySelector('.stask[data-task="${T_NEVER}"]');
      if (!row) return { __err: "row-null" };
      row.click();
      await new Promise((r) => setTimeout(r, 500));
      const t0 = document.getElementById("toast");
      return { toast: t0 ? t0.textContent : "(no el)",
               shown: t0 ? t0.className.includes("show") : false,
               title: document.getElementById("rd-title").textContent };
    })()`);
    check("从未跑过 → toast 指引", !neverRes.__err && neverRes.shown
      && String(neverRes.toast).includes("还没跑过"), JSON.stringify(neverRes));
    check("从未跑过 → 不开详情", !String(neverRes.title || "").includes("从没跑过"),
      JSON.stringify(neverRes));

    // 2) 跑过但最新 run 排队（退避窗口）：直开详情 + chip 显示自动续跑
    const res = await evalJson(`(async () => {
      await new Promise((r) => setTimeout(r, 3600));   // 等上一条 toast 自动消失
      const row = document.querySelector('.stask[data-task="${T_RES}"]');
      if (!row) return { __err: "row-null" };
      row.click();
      await new Promise((r) => setTimeout(r, 1200));
      const t0 = document.getElementById("toast");
      return { title: document.getElementById("rd-title").textContent,
               chip: document.getElementById("rd-status").textContent,
               toastShown: t0 ? t0.className.includes("show") : false,
               toastText: t0 ? t0.textContent : "" };
    })()`);
    check("退避窗口 → 点行直开任务详情", !res.__err && res.title === "退避之书",
      JSON.stringify(res));
    // 上一条 toast 的 textContent 会残留（隐藏只是摘掉 show 类）：
    // 断言「此刻没有正在显示的还没跑过 toast」
    check("退避窗口 → 不再弹「还没跑过」toast",
      !(res.toastShown && String(res.toastText).includes("还没跑过")),
      JSON.stringify(res));
    check("chip 显示「将于 08:30 自动续跑（第 2 次）」",
      String(res.chip || "").includes("自动续跑")
      && String(res.chip || "").includes("08:30")
      && String(res.chip || "").includes("2"),
      res.chip);

    // 3) 预定时刻已过：chip 翻回「排队中」
    const after = await evalJson(`(async () => {
      window.__queuedRun.resume_enqueue_at = "2020-01-01 08:30:00";
      S.taskSig = "";                       // 强制重画
      sideOpenTask("${T_RES}");
      await new Promise((r) => setTimeout(r, 1200));
      return { chip: document.getElementById("rd-status").textContent };
    })()`);
    check("退避窗口已过 → chip 翻回「排队中」", String(after.chip || "").includes("排队中")
      && !String(after.chip || "").includes("自动续跑"), JSON.stringify(after));

    check("无新增控制台错误", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));
  } finally {
    killTree(edge);
    killTree(srv);
    await sleep(800);   // 给 taskkill 一点收尾时间再删目录
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }

  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项失败` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
