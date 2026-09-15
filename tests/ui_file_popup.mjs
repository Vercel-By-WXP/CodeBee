/* 文件内容弹窗核验（Edge headless + CDP，临时服务端口 18818）：
 * 1) 种子：git 仓库工作目录 + 带 changes 快照（多文件 diff、行级统计）的 run + 任务；
 * 2) 主栏 Git 面板：双击 .gf → #file-pop 弹窗只含该文件 diff、行着色、Esc 可关；
 * 3) 检查器 Git 卡：双击 .insp-cf → 弹窗带 ±行数副标题与复制按钮；
 * 4) 成品文件 TAB：行式 chip（扩展名徽标+大小），点开弹内容——md 渲染 /
 *    json 美化 / png 直显 / 二进制给下载提示；英文模式按钮翻译。 */
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18818;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9348;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-filepop-"));
  const dataDir = join(tmp, "data");
  const work = join(tmp, "work");
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(work, { recursive: true });
  const G = (a) => execFileSync("git", a, { cwd: work, encoding: "utf-8" });
  G(["init"]);
  G(["config", "user.name", "Tester"]);
  G(["config", "user.email", "t@l"]);
  writeFileSync(join(work, "README.md"), "baseline\n", "utf-8");
  G(["add", "-A"]); G(["commit", "-m", "baseline"]);
  const taskId = "t" + Date.now().toString(36) + "fpop";
  const runId = "r" + Date.now().toString(36) + "fpop";
  const tb = "tutti/" + taskId;
  G(["checkout", "-q", "-b", tb]);
  writeFileSync(join(work, "chapter-01.md"), "第一章 试炼开始\n清晨的雾还没散。\n", "utf-8");
  G(["add", "-A"]); G(["commit", "-m", "seed artifacts"]);
  G(["checkout", "-q", "master"]);
  // 切回 master 会把分支上跟踪的 chapter-01.md 带走；成品列表按工作目录实存文件列，
  // 补一份未跟踪副本（续跑场景里成稿本来就以未跟踪/新增态躺在工作区）
  writeFileSync(join(work, "chapter-01.md"), "第一章 试炼开始\n清晨的雾还没散。\n", "utf-8");

  // 成品文件（未跟踪，落在工作目录即可被 run_artifacts 列出）
  writeFileSync(join(work, "cover.json"), '{"title":"七猫甜宠","chapters":3}', "utf-8");
  writeFileSync(join(work, "hero.png"), PNG_1PX);
  writeFileSync(join(work, "notes.bin"), Buffer.from([0, 1, 2, 3, 255]));

  const DIFF = [
    "diff --git a/README.md b/README.md",
    "index 1111111..2222222 100644",
    "--- a/README.md",
    "+++ b/README.md",
    "@@ -1,1 +1,2 @@",
    " baseline",
    "+新增一行说明",
    "diff --git a/chapter-01.md b/chapter-01.md",
    "new file mode 100644",
    "index 0000000..3333333",
    "--- /dev/null",
    "+++ b/chapter-01.md",
    "@@ -0,0 +1,2 @@",
    "+第一章 试炼开始",
    "+清晨的雾还没散。",
  ].join("\n");

  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", runId), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "code", engine: "code", title: "文件弹窗种子", goal: "改点东西",
    workdir: work, git_rev: "HEAD", git_state: "isolated", status: "done",
    created_at: "2026-09-14 10:00:00", attachments: [], mode: "auto", difficulty: "auto",
    implementer: "", verify_command: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
    id: runId, kind: "orchestration", title: "文件弹窗种子", task_id: taskId,
    status: "done",
    // 成品口径闸：任务要有步骤才有成品，种子必须带步骤
    steps: [{ n: 1, status: "done", role: "起草", agent_label: "writer",
              summary: "写第一章", duration_s: 12, log: "steps/1.log" }],
    messages: [], created_at: "2026-09-14 10:00:01",
    started_at: "2026-09-14 10:00:01", ended_at: "2026-09-14 10:05:00",
    cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
    git: { rev: "HEAD", branch: tb, commit: "abc1234", from_branch: "master",
           base_commit: "def0123", restored: true, restore_error: "" },
    changes: {
      files: [
        { status: "M", path: "README.md", add: 1, del: 0 },
        { status: "A", path: "chapter-01.md", add: 2, del: 0 }],
      add_total: 3, del_total: 0, diff: DIFF,
    },
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

    const escClose = `document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"})); "ok"`;
    const popState = `JSON.stringify((() => { const p = document.getElementById("file-pop");
      return p ? { open: !p.classList.contains("hidden"),
        title: (p.querySelector(".fp-title")||{}).textContent || "",
        sub: (p.querySelector(".fp-sub")||{}).textContent || "",
        acts: (p.querySelector(".fp-acts")||{}).textContent || "",
        add: p.querySelectorAll(".fp-add").length,
        del: p.querySelectorAll(".fp-del").length,
        meta: p.querySelectorAll(".fp-meta").length,
        body: (p.querySelector(".fp-body")||{}).innerText || "" } : { open: false }; })())`;

    /* 1) 主栏 Git 面板：双击 .gf → 弹窗只含该文件 diff */
    await evalJs(`(async () => { openRun(${JSON.stringify(runId)});
      await new Promise(r => setTimeout(r, 1200)); return 1; })()`);
    const dbl = `(() => { const b = [...document.querySelectorAll("#rd-git .gf")]
      .find(x => x.dataset.p === ${JSON.stringify("README.md")});
      if (!b) return "no-gf";
      b.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); return "ok"; })()`;
    check("主栏 Git 面板：变更行存在", (await evalJs(dbl)) === "ok");
    await sleep(900);
    let p = JSON.parse(await evalJs(popState));
    check("双击 .gf：弹窗打开且标题=文件名", p.open && p.title === "README.md", JSON.stringify(p).slice(0, 200));
    check("diff 切片：只含该文件（有 +行，无别的文件内容）",
      p.add >= 1 && !p.body.includes("第一章"), JSON.stringify({ add: p.add, body: p.body.slice(0, 120) }));
    check("diff 着色：头部行落灰（meta）", p.meta >= 3, "meta=" + p.meta);
    await evalJs(escClose);
    await sleep(200);
    p = JSON.parse(await evalJs(popState));
    check("Esc 关闭弹窗", !p.open);

    /* 2) 检查器 Git 卡：双击 .insp-cf → 副标题 ±统计 + 复制按钮 */
    await evalJs(`(async () => { openInspector(${JSON.stringify(taskId)});
      await new Promise(r => setTimeout(r, 1500)); return 1; })()`);
    await evalJs(`(() => { const b = [...document.querySelectorAll("#insp-git-main .insp-cf")]
      .find(x => x.dataset.p === ${JSON.stringify("chapter-01.md")});
      if (b) b.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); return 1; })()`);
    await sleep(900);
    p = JSON.parse(await evalJs(popState));
    check("检查器变更行双击：弹窗打开、标题=chapter-01.md",
      p.open && p.title === "chapter-01.md", JSON.stringify(p).slice(0, 200));
    check("弹窗副标题 ±行数 + 复制按钮", p.sub.includes("+2") && p.acts.includes("复制"),
      JSON.stringify({ sub: p.sub, acts: p.acts }));
    check("新文件 diff 内容进窗", p.body.includes("第一章 试炼开始"), p.body.slice(0, 100));
    await evalJs(escClose);

    /* 3) 成品文件 TAB：行式 chip + 点开弹内容 */
    await evalJs(`(() => { const b = [...document.querySelectorAll("#insp-tabs .insp-tab")]
      .find(x => x.dataset.tab === "files"); if (b) b.click(); return 1; })()`);
    let filesReady = false;
    for (let i = 0; i < 10 && !filesReady; i++) { await sleep(500); filesReady = await evalJs(
      `!!document.querySelector("#insp-pane-files .files-head")`); }
    check("成品 TAB：loadArtifacts 渲染完成", filesReady);
    const chips = JSON.parse(await evalJs(`(() => JSON.stringify({
      n: document.querySelectorAll("#insp-pane-files .file-chip:not(.prev)").length,
      fx: !!document.querySelector("#insp-pane-files .file-chip .fx"),
      p: !!document.querySelector("#insp-pane-files .file-chip .p"),
    }))()`));
    check("成品行样式：扩展名徽标 + 文件名列 + 同款行式布局", chips.n >= 4 && chips.fx && chips.p, JSON.stringify(chips));

    const clickChip = async (name) => evalJs(`(() => { const c = [...document
      .querySelectorAll("#insp-pane-files .file-chip:not(.prev)")]
      .find(x => x.querySelector(".p") && x.querySelector(".p").textContent === ${JSON.stringify(name)});
      if (!c) return "no-chip"; c.click(); return "ok"; })()`);
    const mdStates = [
      ["chapter-01.md", async () => {
        const q = JSON.parse(await evalJs(popState));
        return q.open && !!(await evalJs(`!!document.querySelector("#file-pop .fp-md")`)) &&
          q.body.includes("第一章 试炼开始");
      }, "md 渲染进弹窗"],
      ["cover.json", async () => {
        const q = JSON.parse(await evalJs(popState));
        return q.open && !!(await evalJs(`!!document.querySelector("#file-pop .fp-code pre")`)) &&
          q.body.includes('"title"') && q.body.includes("\n  ");
      }, "json 美化缩进"],
      ["hero.png", async () => !!(await evalJs(
        `(() => { const i = document.querySelector("#file-pop .fp-img img"); return i && i.src.startsWith("blob:"); })()`)),
        "png blob 直显"],
      ["notes.bin", async () => {
        const q = JSON.parse(await evalJs(popState));
        return q.open && q.body.includes("二进制");
      }, "二进制给下载提示"],
    ];
    for (const [name, fn, label] of mdStates) {
      check("chip 存在：" + name, (await clickChip(name)) === "ok");
      await sleep(900);
      check("点开弹内容：" + label, await fn());
      await evalJs(escClose); await sleep(150);
    }

    /* 4) 英文模式：按钮翻译 */
    await evalJs(`localStorage.setItem("orch.lang","en");applyI18n&&applyI18n(); "ok"`);
    await clickChip("chapter-01.md");
    await sleep(900);
    p = JSON.parse(await evalJs(popState));
    check("英文模式：下载/复制按钮翻译", p.acts.includes("Download") && p.acts.includes("Copy"), p.acts);
    await evalJs(`localStorage.setItem("orch.lang","zh"); "ok"`);
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { if (svc) svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* 进程未退干净则留给系统临时目录 */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
