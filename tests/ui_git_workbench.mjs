/* GIT 工作台核验（Edge headless + CDP，临时服务端口 18836，CDP 9396——避开
 * 18798/9356 等既有端口与并行测试的 CDP 调试口）：
 * 1) 种子：git 仓库（预建 dev 分支）+ 已暂存/未暂存/未跟踪三类现场 + 任务/run；
 * 2) 打开 run 详情 → Git 页签常驻可见：分支下拉、工具条（无远程时网络钮禁用）、
 *    三组文件、提交区；
 * 3) 点文件行 → #file-pop 实时 diff 弹窗（行着色），Esc 可关；
 * 4) 悬停「暂存」点击 → 未暂存进暂存组；填提交信息点提交 → 暂存清空、git log 落库；
 * 5) 分支下拉切 dev → 工作区真实切到 dev；
 * 6) stash 收起 → 条目出现 → 还原 → 文件回来；
 * 7) 英文模式关键文案翻译；全程无 JS 异常。 */
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18836;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9396;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-gitwb-"));
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
  G(["branch", "dev"]);
  // 三类现场：已暂存 / 未暂存 / 未跟踪
  writeFileSync(join(work, "staged.txt"), "已暂存内容\n", "utf-8");
  G(["add", "staged.txt"]);
  writeFileSync(join(work, "README.md"), "baseline\n未暂存追加\n", "utf-8");
  writeFileSync(join(work, "notes.md"), "随手记\n", "utf-8");

  const taskId = "t" + Date.now().toString(36) + "gitwb";
  const runId = "r" + Date.now().toString(36) + "gitwb";
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", runId), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "code", engine: "code", title: "工作台种子", goal: "改点东西",
    workdir: work, status: "done",
    created_at: "2026-09-16 10:00:00", attachments: [], mode: "auto", difficulty: "auto",
    implementer: "", verify_command: "",
  }), "utf-8");
  writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
    id: runId, kind: "orchestration", title: "工作台种子", task_id: taskId,
    status: "done",
    steps: [{ n: 1, status: "done", role: "起草", agent_label: "writer",
              summary: "改一步", duration_s: 5, log: "steps/1.log" }],
    messages: [], created_at: "2026-09-16 10:00:01",
    started_at: "2026-09-16 10:00:01", ended_at: "2026-09-16 10:05:00",
    cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }), "utf-8");

  let svc = null, edge = null, ws = null;
  const kill = (p) => { try { execFileSync("taskkill", ["/F", "/T", "/PID", String(p.pid)]); } catch (e) { /* already gone */ } };
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
    let seq = 0; const pending = new Map(); const jsErrors = [];
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    send("Runtime.enable").then(() => {
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id && pending.has(m.id)) pending.get(m.id)(m);
        if (m.method === "Runtime.exceptionThrown") jsErrors.push(JSON.stringify(m.params).slice(0, 200));
      };
    });
    await send("Page.enable");
    await send("Runtime.evaluate", { expression: `window.alert=()=>true;window.confirm=()=>true;` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 1) 打开 run 详情 → 工作台落位 */
    await evalJs(`openRun(${JSON.stringify(runId)}); true`);
    await evalJs(`(async () => { for (let i = 0; i < 30; i++) { await new Promise(r => setTimeout(r, 300));
      const b = document.getElementById("gwb-branch");
      if (b) return true; } return false; })()`);
    await evalJs(`(function(){ const b=[...document.querySelectorAll("#rd-tabs .rd-tab")].find(x=>x.dataset.tab==="git"); if(b&&!b.classList.contains("hidden")) b.click(); })()`);
    await sleep(600);
    const s1 = JSON.parse(await evalJs(`(() => {
      const box = document.getElementById("rd-git");
      if (!box) return JSON.stringify({ exists: false });
      const sel = box.querySelector("#gwb-branch");
      const group = (k) => [...box.querySelectorAll('.gwb-group[data-kind="' + k + '"] .gf .p')].map(e => e.textContent);
      const dis = (label) => { const b = [...box.querySelectorAll(".gwb-toolbar button")].find(x => x.textContent === label); return b ? b.disabled : null; };
      return JSON.stringify({
        exists: true, hidden: box.classList.contains("hidden"),
        title: box.querySelector(".sec-title")?.textContent,
        branch: sel ? sel.value : null,
        tabVisible: ![...document.querySelectorAll("#rd-tabs .rd-tab")].find(x => x.dataset.tab === "git")?.classList.contains("hidden"),
        staged: group("staged"), unstaged: group("unstaged"), untracked: group("untracked"),
        fetchDis: dis("抓取"), pushDis: dis("推送"),
        commitDisabled: box.querySelector(".gwb-commit .primary")?.disabled,
        head: box.querySelector(".git-head code")?.textContent,
      });
    })()`));
    check("#rd-git 工作台渲染且可见", s1.exists && !s1.hidden, JSON.stringify(s1));
    check("Git 页签常驻可见", s1.tabVisible === true, JSON.stringify(s1.tabVisible));
    check("标题=Git 工作台，分支下拉=master", s1.title === "Git 工作台" && s1.branch === "master", JSON.stringify(s1));
    check("HEAD 短哈希在场", /^[0-9a-f]{7,}$/.test(s1.head || ""), s1.head);
    check("三组分类：暂存/未暂存/未跟踪各 1",
      s1.staged.join() === "staged.txt" && s1.unstaged.join() === "README.md" && s1.untracked.join() === "notes.md",
      JSON.stringify({ s: s1.staged, u: s1.unstaged, n: s1.untracked }));
    check("无远程：抓取/推送禁用", s1.fetchDis === true && s1.pushDis === true, JSON.stringify({ f: s1.fetchDis, p: s1.pushDis }));
    check("暂存区有货：提交按钮可用", s1.commitDisabled === false, JSON.stringify(s1.commitDisabled));

    /* 2) 点文件行 → 实时 diff 弹窗 */
    const s2 = JSON.parse(await evalJs(`(async () => {
      const row = [...document.querySelectorAll('.gwb-group[data-kind="unstaged"] .gf')]
        .find(e => e.dataset.p === "README.md");
      if (!row) return JSON.stringify({ ok: false });
      row.click();
      await new Promise(r => setTimeout(r, 800));
      const pop = document.getElementById("file-pop");
      const out = {
        ok: true, open: !!(pop && !pop.classList.contains("hidden")),
        title: pop?.querySelector(".fp-title")?.textContent,
        addLines: pop ? [...pop.querySelectorAll(".fp-add")].length : 0,
      };
      if (out.open) window.filePopClose();
      return JSON.stringify(out);
    })()`));
    check("点文件行弹实时 diff（+行着色）", s2.open && s2.title === "README.md" && s2.addLines >= 1, JSON.stringify(s2));

    /* 3) 悬停暂存 → 未暂存进暂存组 */
    await evalJs(`(async () => {
      const row = [...document.querySelectorAll('.gwb-group[data-kind="unstaged"] .gwb-row')]
        .find(e => e.querySelector(".gf")?.dataset.p === "README.md");
      row.querySelector(".gwb-act").click();
      for (let i = 0; i < 20; i++) { await new Promise(r => setTimeout(r, 300));
        const g = [...document.querySelectorAll('.gwb-group[data-kind="staged"] .gf .p')].map(e => e.textContent);
        if (g.includes("README.md")) return true; }
      return false;
    })()`);
    const s3 = JSON.parse(await evalJs(`(() => {
      const box = document.getElementById("rd-git");
      const group = (k) => [...box.querySelectorAll('.gwb-group[data-kind="' + k + '"] .gf .p')].map(e => e.textContent);
      return JSON.stringify({ staged: group("staged"), unstaged: group("unstaged") });
    })()`));
    check("点击暂存：README.md 进暂存组", s3.staged.includes("README.md") && !s3.unstaged.includes("README.md"), JSON.stringify(s3));

    /* 4) 提交：填信息 → 点提交 → 暂存清空 → git log 落库 */
    const committed = await evalJs(`(async () => {
      const mi = document.getElementById("gwb-msg");
      mi.value = "工作台提交";
      document.querySelector(".gwb-commit .primary").click();
      for (let i = 0; i < 20; i++) { await new Promise(r => setTimeout(r, 300));
        const g = [...document.querySelectorAll('.gwb-group[data-kind="staged"] .gf .p')].map(e => e.textContent);
        if (!g.length) return true; }
      return false;
    })()`);
    check("提交后暂存区清空", committed === true, JSON.stringify(committed));
    const logMsg = execFileSync("git", ["log", "-1", "--format=%s"], { cwd: work, encoding: "utf-8" }).trim();
    check("提交落库（信息一致）", logMsg === "工作台提交", logMsg);

    /* 5) 分支下拉切 dev（等 S.gitWb.branch 翻转——后端真切换完成才回来） */
    const switched = await evalJs(`(async () => {
      const sel = document.getElementById("gwb-branch");
      sel.value = "dev"; sel.dispatchEvent(new Event("change"));
      for (let i = 0; i < 20; i++) { await new Promise(r => setTimeout(r, 300));
        if (typeof S !== "undefined" && S.gitWb && S.gitWb.branch === "dev") return true; }
      return false;
    })()`);
    const realBranch = execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], { cwd: work, encoding: "utf-8" }).trim();
    check("分支下拉切到 dev（下拉与真实仓库一致）", switched === true && realBranch === "dev", JSON.stringify({ switched, realBranch }));
    G(["checkout", "-q", "master"]);   // 还原现场供 stash 断言

    /* 6) stash 收起 → 还原 */
    writeFileSync(join(work, "wip.txt"), "半成品\n", "utf-8");
    await evalJs(`loadGitWb(true)`);
    await sleep(700);
    const stashed = await evalJs(`(async () => {
      document.querySelector(".gwb-toolbar button[title*='stash']")?.click();
      for (let i = 0; i < 20; i++) { await new Promise(r => setTimeout(r, 300));
        if (document.querySelector(".gwb-stash")) return true; }
      return false;
    })()`);
    check("stash 收起后条目出现", stashed === true, JSON.stringify(stashed));
    const restored = await evalJs(`(async () => {
      document.querySelector(".gwb-stash .ghost")?.click();
      for (let i = 0; i < 20; i++) { await new Promise(r => setTimeout(r, 300));
        if (!document.querySelector(".gwb-stash")) return true; }
      return false;
    })()`);
    check("stash 还原后条目消失", restored === true, JSON.stringify(restored));
    check("stash 还原：文件回到工作区", existsSync(join(work, "wip.txt")), "");

    /* 7) 英文模式 */
    await evalJs(`localStorage.setItem("orch.lang","en");applyI18n&&applyI18n();loadGitWb(true)`);
    await sleep(1200);
    const s7 = JSON.parse(await evalJs(`(() => {
      const box = document.getElementById("rd-git");
      const btn = (label) => [...box.querySelectorAll(".gwb-toolbar button")].some(x => x.textContent === label);
      return JSON.stringify({ title: box?.querySelector(".sec-title")?.textContent,
        fetch: btn("Fetch"), pull: btn("Pull"), push: btn("Push"),
        staged: box?.querySelector('.gwb-group[data-kind="staged"] .gwb-ghead b')?.textContent });
    })()`));
    check("英文模式：Git Workbench / Fetch / Pull / Push / Staged changes",
      s7.title === "Git Workbench" && s7.fetch && s7.pull && s7.push && s7.staged === "Staged changes", JSON.stringify(s7));

    check("全程无 JS 异常", jsErrors.length === 0, jsErrors.join(" | "));
  } finally {
    if (edge) kill(edge);
    if (svc) kill(svc);
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    await sleep(300);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* Windows 句柄延迟 */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? "\nFAILED " + bad.length + "/" + results.length : "\nOK " + results.length + "/" + results.length);
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(2); });
