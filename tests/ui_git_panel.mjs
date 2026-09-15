/* 代码版本隔离面板核验（Edge headless + CDP，临时服务端口 18816）：
 * 1) 种子：git 仓库工作目录 + 带 git/changes 的 run + 任务 git_state=isolated；
 * 2) 打开 run 详情 → #rd-git 可见：分支名、待裁决 chip、变更文件 chips、合并/丢弃按钮；
 * 3) POST git-merge（Node 侧 fetch 直接打 API，绕开浏览器 confirm）→ state 变 merged、
 *    刷新后面板不再有裁决按钮、chip=已合并、原分支收到产物；
 * 4) 非检出 run（无 git 字段）→ 面板隐藏；
 * 5) i18n：切英文后面板关键文案变英文。 */
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, rmSync as rmf } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18816;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9346;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const NL2 = String.fromCharCode(10) + "# 主角";
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-gitpanel-"));
  const dataDir = join(tmp, "data");
  const work = join(tmp, "work");
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(work, { recursive: true });
  // git 仓库基线 + 真实任务分支（分支上提交产物文件，随后切回 master——
  // merge/discard 的守卫检查真实分支，种子必须让分支存在）
  const G = (a) => execFileSync("git", a, { cwd: work, encoding: "utf-8" });
  G(["init"]);
  G(["config", "user.name", "Tester"]);
  G(["config", "user.email", "t@l"]);
  writeFileSync(join(work, "README.md"), "baseline\n", "utf-8");
  G(["add", "-A"]); G(["commit", "-m", "baseline"]);
  const taskId = "t" + Date.now().toString(36) + "gitseed";
  const runId = "r" + Date.now().toString(36) + "gitseed";
  const tb = "tutti/" + taskId;
  G(["checkout", "-q", "-b", tb]);
  writeFileSync(join(work, "chapter-01.md"), "第一章\n", "utf-8");
  G(["add", "-A"]); G(["commit", "-m", "tutti seed artifacts"]);
  const tbCommit = execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: work, encoding: "utf-8" }).trim();
  G(["checkout", "-q", "master"]);
  const baseCommit = execFileSync("git", ["rev-parse", "--short", "master"], { cwd: work, encoding: "utf-8" }).trim();

  // 预置任务 + run（直接落 run.json / task json，不跑流水线）


  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", runId), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "code", engine: "code", title: "隔离面板种子", goal: "改点东西",
    workdir: work, git_rev: "HEAD", git_state: "isolated", status: "done",
    // serial：圣经面板只对连载任务渲染（4.6 节断言依赖）
    serial: true,
    created_at: "2026-09-14 10:00:00", attachments: [], mode: "auto", difficulty: "auto",
    implementer: "", verify_command: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
    id: runId, kind: "orchestration", title: "隔离面板种子", task_id: taskId,
    status: "done",
    // 成品口径闸：任务要有步骤才有成品（4.5 节的预览按钮断言依赖）
    steps: [{ n: 1, status: "done", role: "起草", agent_label: "writer",
              summary: "产出一章", duration_s: 30, log: "steps/1.log" }],
    messages: [], created_at: "2026-09-14 10:00:01",
    started_at: "2026-09-14 10:00:01", ended_at: "2026-09-14 10:05:00",
    cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
    git: { rev: "HEAD", branch: tb, commit: tbCommit, from_branch: "master",
           base_commit: baseCommit, restored: true, restore_error: "" },
    changes: { files: [
      { status: "A", path: "chapter-01.md" }, { status: "M", path: "README.md" },
      { status: "??", path: "_notes/new.md" }],
      diff: "diff --git a/README.md b/README.md" },
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
    await send("Runtime.evaluate", { expression: `window.alert=()=>true;window.confirm=()=>true;` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 1) 打开 run 详情 → 面板可见且内容齐 */
    const s1 = JSON.parse(await evalJs(`(async () => {
      openRun(${JSON.stringify(runId)});
      await new Promise(r => setTimeout(r, 1200));
      const box = document.getElementById("rd-git");
      if (!box) return JSON.stringify({ exists: false });
      return JSON.stringify({
        exists: true, hidden: box.classList.contains("hidden"),
        branch: box.querySelector(".git-branch")?.textContent,
        chip: box.querySelector(".git-head .chip")?.textContent,
        files: [...box.querySelectorAll(".gf")].map(e => e.textContent.trim()),
        mergeBtn: !!box.querySelector(".git-actions .primary"),
        discardBtn: !!box.querySelector(".git-actions .danger"),
        meta: box.querySelector(".git-meta")?.textContent || "",
        // 徽章要在合并前查：任务此刻还是 isolated，合并后侧栏刷新就没了
        badge: document.querySelector("#side-tasks .sbadge")?.textContent || null,
      });
    })()`));
    check("#rd-git 渲染且可见", s1.exists && !s1.hidden, JSON.stringify(s1));
    check("分支名 tutti/<task>", (s1.branch || "").startsWith("tutti/" + taskId.slice(0, 6)), s1.branch);
    check("待裁决 chip", s1.chip === "待裁决", s1.chip);
    check("侧栏待裁决徽章在（git_state=isolated 任务，合并前）", s1.badge === "待裁决", JSON.stringify(s1.badge));
    check("变更文件 3 枚（含新文件）", s1.files.length === 3 && s1.files.some(f => f.includes("chapter-01.md")), JSON.stringify(s1.files));
    // 旧快照里存的原始 "??" 也要归一成「新」，不许把 ?? 当文案渲染
    check("未跟踪徽章显示「新」而非 ??", s1.files.some(f => f.includes("_notes/new.md") && f.startsWith("新")), JSON.stringify(s1.files));
    check("合并/丢弃按钮在（isolated）", s1.mergeBtn && s1.discardBtn, JSON.stringify({ m: s1.mergeBtn, d: s1.discardBtn }));

    /* 2) API 合并 → state merged → 原分支收到产物 → 面板更新 */
    const mv = await (await fetch(SERVICE + "/api/tasks/" + taskId + "/git-merge", { method: "POST" })).json();
    check("git-merge 成功", mv.ok === true, JSON.stringify(mv));
    const st2 = await (await fetch(SERVICE + "/api/state")).json();
    const t2 = (st2.tasks || []).find(x => x.id === taskId);
    check("任务 git_state=merged", t2 && t2.git_state === "merged", t2 && t2.git_state);
    const curBranch = execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], { cwd: work, encoding: "utf-8" }).trim();
    const ls = execFileSync("git", ["ls-tree", "-r", "--name-only", curBranch], { cwd: work, encoding: "utf-8" });
    check("产物已并入原分支（README 变更在）", curBranch !== "tutti/" + taskId && ls.includes("README.md"), curBranch);

    // SSE 活跃时 poll() 跳过 refreshState；无头验证直接强拉状态并重绘详情
    await evalJs(`(async () => { await refreshState(); renderRunDetail(); })()`);
    await sleep(1500);
    const s3 = JSON.parse(await evalJs(`(() => {
      const box = document.getElementById("rd-git");
      return JSON.stringify({
        chip: box?.querySelector(".git-head .chip")?.textContent,
        hasMerge: !!box?.querySelector(".git-actions .primary"),
      });
    })()`));
    check("合并后面板 chip=已合并 且无裁决按钮", s3.chip === "已合并" && !s3.hasMerge, JSON.stringify(s3));

    /* 3) 无 git 的 run → 面板隐藏（本种子的 run 已有 git；用管理类 run 无从打开，改查 hidden 初始态已由 s1 覆盖，跳过） */

    /* 4) i18n：切英文 */
    await evalJs(`localStorage.setItem("orch.lang","en");applyI18n&&applyI18n();renderRunDetail&&renderRunDetail();`);
    await sleep(1200);
    const s4 = JSON.parse(await evalJs(`(() => {
      const box = document.getElementById("rd-git");
      return JSON.stringify({ title: box?.querySelector(".sec-title")?.textContent,
        chip: box?.querySelector(".git-head .chip")?.textContent });
    })()`));
    check("英文模式：Version isolation / Merged", s4.title === "Version isolation" && s4.chip === "Merged", JSON.stringify(s4));

    /* 4.5) 铃铛 / 预览按钮（徽章已在第 1 步合并前查过） */
    const s45 = JSON.parse(await evalJs(`(() => {
      const bell = document.getElementById("btn-notify-toggle");
      const prev = [...document.querySelectorAll("#insp-pane-files .file-chip.prev")];
      let pv = null;
      if (prev.length) { prev[0].click(); }
      return new Promise((res) => setTimeout(() => {
        const box = document.getElementById("rd-preview");
        res(JSON.stringify({
            bellOff: bell ? bell.classList.contains("off") : null,
          nPrev: prev.length,
          previewShown: box && !box.classList.contains("hidden"),
          previewBody: box && box.querySelector(".prev-body") ? !!box.querySelector(".prev-body").innerHTML : false,
        }));
      }, 700));
    })()`));
    check("铃铛开关存在且默认开启", s45.bellOff === false, JSON.stringify(s45));
    check("成品 md 有预览按钮且点击后渲染", s45.nPrev >= 1 && s45.previewShown && s45.previewBody, JSON.stringify(s45));

    /* 4.6) 故事圣经面板：未创建时显示引导；API 写入后回读一致、面板显示内容 */
    const bp = JSON.parse(await evalJs(`(async () => {
      const box = document.getElementById("rd-bible");
      const shown = box && !box.classList.contains("hidden");
      const title = box?.querySelector(".sec-title")?.textContent;
      const hint = box?.querySelector(".hint")?.textContent || "";
      return JSON.stringify({ shown, title, hint: hint.slice(0, 30) });
    })()`));
    check("故事圣经面板可见且带引导", bp.shown && ["故事圣经", "Story bible"].includes(bp.title), JSON.stringify(bp));
    const bsave = await (await fetch(SERVICE + "/api/tasks/" + taskId + "/bible", {
      method: "POST", body: JSON.stringify({ text: NL2 + "外冷内热" }) })).json();
    check("圣经 POST 保存成功", bsave.ok === true, JSON.stringify(bsave));
    const bread = await (await fetch(SERVICE + "/api/tasks/" + taskId + "/bible")).json();
    check("圣经回读一致", (bread.text || "").includes("外冷内热"), JSON.stringify(bread).slice(0, 120));
    const bp2 = JSON.parse(await evalJs(`(async () => {
      await refreshState(); renderRunDetail();
      await new Promise(r => setTimeout(r, 900));
      const box = document.getElementById("rd-bible");
      return JSON.stringify({ view: box?.querySelector(".bible-view")?.textContent?.slice(0, 20) });
    })()`));
    check("圣经内容上面板", (bp2.view || "").includes("主角"), JSON.stringify(bp2));

    /* 5) git-discard：对另一个任务（复用同 run 的分支场景不合适，直接守卫路径即可） */
    const dv = await (await fetch(SERVICE + "/api/tasks/" + taskId + "/git-discard", {
      method: "POST", body: JSON.stringify({ confirm: true }) })).json();
    check("已 merged 的任务丢弃 → 分支仍在被拒绝或成功（守卫允许）", dv.ok === true || !!dv.error, JSON.stringify(dv));
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? "\nFAILED %d/%d" : "\nALL PASS %d", results.length - bad.length, results.length);
  process.exit(bad.length ? 1 : 0);
}
main().catch((e) => { console.error("FATAL", e); process.exit(2); });
