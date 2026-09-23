/* 一次性核验：详情页「✎ 编辑重试」按钮 + 按钮区压缩（Edge headless + CDP，临时端口 18835）。
 * 失败/取消任务 → 显示 编辑重试、隐藏 基于此任务新建；完成态 → 反之。
 * 连载完成但未达标（verdict.publishable=false）→ 「↻ 重写未达标章」入口
 * （任务级/run 级/右键菜单三处；达标收尾不出，英文模式有词条）。
 * 点编辑重试 → 回表单且预填、焦点落目标框；删除记录 → 细身右对齐。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18835;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9357;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-editretry-"));
  const dataDir = join(tmp, "data");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  const mk = (taskId, runId, title, status, runErr, steps, verdict = null) => {
    writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
      id: taskId, type: "code", engine: "code", title, goal: "造数：" + title,
      workdir: join(tmp, "wd-" + taskId), git_rev: "", git_state: "",
      status, created_at: "2026-09-15 10:00:00", attachments: [],
      mode: "auto", difficulty: "auto", implementer: "", verify_command: "",
    }), "utf-8");
    mkdirSync(join(dataDir, "runs", runId), { recursive: true });
    writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
      id: runId, kind: "orchestration", title, task_id: taskId,
      status, steps: steps || [], messages: [], created_at: "2026-09-15 10:00:01",
      started_at: "2026-09-15 10:00:01", ended_at: "2026-09-15 10:00:30",
      cost_usd: 0, tokens: 0,
      error: runErr != null ? runErr : (status === "failed" ? "工作区有未提交改动" : ""),
      verdict, summary: "", git: null,
    }), "utf-8");
  };
  mk("tedit1", "redit1", "失败态任务", "failed", null,
    [{ n: 1, role: "plan", agent: "a1", agent_label: "Codex CLI", status: "cancelled",
       summary: "计划已取消", started_at: "10:00:02", ended_at: "10:00:30",
       duration_s: 28.0, log: "" }]);
  mk("tdone1", "rdone1", "完成态任务", "done");
  mk("tquality1", "rquality1", "验收未通过任务", "done", "", [],
    { pass: false, verify_pass: false, review_pass: true });
  // 超时场景：run 级仍是 failed（状态机不扩），错误文案「超时」打头 + 步骤记 timeout
  mk("ttime1", "rtime1", "超时任务", "failed", "超时 1200s，已终止进程树；stderr/stdout: ……",
    [{ n: 1, role: "implement", agent: "a1", agent_label: "Codex CLI", status: "timeout",
       summary: "超时 1200s，已终止进程树", started_at: "10:00:02", ended_at: "10:20:02",
       duration_s: 1200.0, log: "" }]);

  // 连载未达标：完成态但 verdict.publishable=false → 「↻ 重写未达标章」入口
  const mkSerial = (taskId, runId, title, publishable) => {
    writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
      id: taskId, type: "serial_novel", engine: "code", title, goal: "造数：" + title,
      workdir: join(tmp, "wd-" + taskId), git_rev: "", git_state: "",
      status: "done", created_at: "2026-09-15 10:00:00", attachments: [],
      mode: "auto", difficulty: "auto", implementer: "", verify_command: "",
      serial: { chapters: 2, words_per_chapter: 800 },
    }), "utf-8");
    mkdirSync(join(dataDir, "runs", runId), { recursive: true });
    writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
      id: runId, kind: "orchestration", title, task_id: taskId,
      status: "done", steps: [], messages: [], created_at: "2026-09-15 10:00:01",
      started_at: "2026-09-15 10:00:01", ended_at: "2026-09-15 10:00:30",
      cost_usd: 0, tokens: 0, error: "",
      summary: publishable ? "连载任务达标" : "连载任务未达标",
      git: null,
      verdict: { serial: true, publishable, overall: publishable ? 7.8 : 6.9,
        chapter_scores: [
          { chapter: 1, title: "第 1 章", means: { 情节: 7.5 }, passed: true, rounds: 1, words: 800 },
          { chapter: 2, title: "第 2 章", means: { 情节: publishable ? 7.6 : 6.0 },
            passed: publishable, rounds: publishable ? 1 : 2, words: 800 }] },
    }), "utf-8");
  };
  mkSerial("tserial1", "rserial1", "连载未达标任务", false);
  mkSerial("tserial2", "rserial2", "连载达标任务", true);

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
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
    await evalJs(`localStorage.setItem("orch.lang", "zh"); location.reload()`);
    await sleep(2500);

    const btnsOf = `(() => { const g = (id) => { const b = document.getElementById(id);
      return b ? { vis: !b.classList.contains("hidden"), txt: b.textContent.trim() } : { vis: false, txt: "" }; };
      return JSON.stringify({ retry: g("btn-retry"), edit: g("btn-editretry"), nf: g("btn-newfrom"), del: g("btn-delete") }); })()`;

    // 失败态任务详情（任务级路径）
    await evalJs(`sideOpenTask("tedit1")`);
    await sleep(1200);
    let bs = JSON.parse(await evalJs(btnsOf));
    check("失败态：继续任务可见", bs.retry.vis, JSON.stringify(bs));
    check("失败态：编辑重试可见且文案正确", bs.edit.vis && bs.edit.txt.includes("编辑重试"), JSON.stringify(bs.edit));
    check("失败态：基于此任务新建隐藏（与编辑重试互斥）", !bs.nf.vis, JSON.stringify(bs));

    // run 级详情（用户截图的页面）：删除记录在此层显示，验证压缩样式
    await evalJs(`openRun("redit1")`);
    await sleep(1200);
    bs = JSON.parse(await evalJs(btnsOf));
    check("run 级失败态：编辑重试可见、新建隐藏", bs.edit.vis && !bs.nf.vis, JSON.stringify(bs));
    check("run 级失败态：删除记录可见", bs.del.vis, JSON.stringify(bs));

    // 蜂巢格：已取消步骤不再冒充「完成」（琥珀边框 + 已取消标签 + 泳道圆点）
    const hz = JSON.parse(await evalJs(`JSON.stringify({
      cell: !!document.querySelector("#rd-hive-cells .hive-cell.st-cancelled"),
      dead: (document.querySelector("#rd-hive-cells .hc-dead") || {}).textContent || "",
      dot: !!document.querySelector("#rd-hive-cells .ld-cancel") })`));
    check("蜂巢格：取消步骤带取消样式/标签/圆点",
      hz.cell && hz.dead.includes("已取消") && hz.dot, JSON.stringify(hz));

    // 侧栏右键菜单：失败任务的入口说法与详情页一致（✎ 编辑重试）
    const ctx = JSON.parse(await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .stask[data-task="tedit1"]');
      if (!det) return JSON.stringify({ err: "no row" });
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const labels = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .map(x => x.textContent.trim());
      document.body.click();
      return JSON.stringify({ labels }); })()`));
    check("右键菜单：失败任务显示「✎ 编辑重试」而非「基于此任务新建」",
      ctx.labels && ctx.labels.some((l) => l.includes("编辑重试"))
        && !ctx.labels.includes("基于此任务新建"), JSON.stringify(ctx));

    // 删除记录压缩为细身右对齐
    const delStyle = JSON.parse(await evalJs(`(() => { const b = document.getElementById("btn-delete");
      const row = b.closest(".rd-actions");
      const cs = getComputedStyle(b);
      return JSON.stringify({ fs: cs.fontSize, w: b.getBoundingClientRect().width,
        rowW: row.getBoundingClientRect().width,
        right: Math.abs(row.getBoundingClientRect().right - b.getBoundingClientRect().right) }); })()`));
    check("删除记录：细身（≤12px 小字号）", delStyle.fs <= "12px" || parseFloat(delStyle.fs) <= 12, JSON.stringify(delStyle));
    check("删除记录：宽度不足按钮区一半（不再满宽）",
      delStyle.w < delStyle.rowW / 2, JSON.stringify(delStyle));
    check("删除记录：右对齐（贴按钮区右缘）", delStyle.right < 2, JSON.stringify(delStyle));

    // 点编辑重试 → 表单预填 + 焦点落目标框
    await evalJs(`document.getElementById("btn-editretry").click()`);
    await sleep(800);
    const pf = JSON.parse(await evalJs(`JSON.stringify({
      formVisible: !document.getElementById("page-settings").classList.contains("hidden"),
      goal: document.getElementById("f-goal").value,
      title: document.getElementById("f-title").value,
      focusGoal: document.activeElement === document.getElementById("f-goal") })`));
    check("点编辑重试：回到新建表单且预填", pf.formVisible && pf.goal === "造数：失败态任务"
      && pf.title === "失败态任务", JSON.stringify(pf));
    check("点编辑重试：焦点落在目标输入框", pf.focusGoal, JSON.stringify(pf));

    // 完成态任务详情：回到原标签
    await evalJs(`sideOpenTask("tdone1")`);
    await sleep(1200);
    bs = JSON.parse(await evalJs(btnsOf));
    check("完成态：基于此任务新建可见", bs.nf.vis, JSON.stringify(bs));
    check("完成态：编辑重试隐藏", !bs.edit.vis, JSON.stringify(bs));
    check("完成态：继续任务隐藏", !bs.retry.vis, JSON.stringify(bs));

    // 代码实现完成但本地验证未通过：流水线仍为 done，质量门禁结果必须开放重试。
    await evalJs(`sideOpenTask("tquality1")`);
    await sleep(1200);
    bs = JSON.parse(await evalJs(btnsOf));
    check("验收未通过（done + verify_pass=false）：重试可见", bs.retry.vis, JSON.stringify(bs));
    check("验收未通过：按钮明确显示重试任务", bs.retry.txt.includes("重试任务"), JSON.stringify(bs.retry));

    // 连载未达标（2026-09-20 重写未达标章）：完成态但 verdict.publishable=false
    // → 任务级与 run 级详情都放行重试按钮，文案换「↻ 重写未达标章」
    await evalJs(`sideOpenTask("tserial1")`);
    await sleep(1200);
    bs = JSON.parse(await evalJs(btnsOf));
    check("连载未达标（任务级）：重写未达标章可见且文案正确",
      bs.retry.vis && bs.retry.txt.includes("重写未达标章"), JSON.stringify(bs.retry));
    await evalJs(`openRun("rserial1")`);
    await sleep(1200);
    bs = JSON.parse(await evalJs(btnsOf));
    check("连载未达标（run 级）：重写未达标章可见",
      bs.retry.vis && bs.retry.txt.includes("重写未达标章"), JSON.stringify(bs.retry));
    // 对照：连载达标收尾 → 不出重写入口
    await evalJs(`sideOpenTask("tserial2")`);
    await sleep(1200);
    bs = JSON.parse(await evalJs(btnsOf));
    check("连载达标：无重试入口", !bs.retry.vis, JSON.stringify(bs.retry));
    // 侧栏右键菜单：连载未达标任务带「↻ 重写未达标章」
    const ctxRw = JSON.parse(await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .stask[data-task="tserial1"]');
      if (!det) return JSON.stringify({ err: "no row" });
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const labels = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .map(x => x.textContent.trim());
      document.body.click();
      return JSON.stringify({ labels }); })()`));
    check("右键菜单：连载未达标显示「↻ 重写未达标章」",
      ctxRw.labels && ctxRw.labels.some((l) => l.includes("重写未达标章")), JSON.stringify(ctxRw));

    // 超时场景（放最后，避免打乱前面预填断言的当前任务）：
    // 错误框带 TIMEOUT 徽标；步骤芯片显示「超时」（rd-steps 行内是 chip，无 sdot）
    await evalJs(`openRun("rtime1")`);
    await sleep(1200);
    const tout = JSON.parse(await evalJs(`JSON.stringify({
      badge: !!document.querySelector("#rd-meta .err-tag"),
      chipCls: (document.querySelector("#rd-steps .chip.timeout") || {}).className || "",
      chip: (document.querySelector("#rd-steps .chip.timeout") || {}).textContent || "" })`));
    check("超时运行：错误框带 TIMEOUT 徽标", tout.badge, JSON.stringify(tout));
    check("超时步骤：芯片带 timeout 样式且显示「超时」",
      tout.chipCls.includes("timeout") && tout.chip.includes("超时"), JSON.stringify(tout));

    // 蜂巢格：超时步骤紫色边框 + ⏱ 标签 + 泳道圆点
    const ht = JSON.parse(await evalJs(`JSON.stringify({
      cell: !!document.querySelector("#rd-hive-cells .hive-cell.st-timeout"),
      dead: (document.querySelector("#rd-hive-cells .hc-dead") || {}).textContent || "",
      dot: !!document.querySelector("#rd-hive-cells .ld-time") })`));
    check("蜂巢格：超时步骤带超时样式/标签/圆点",
      ht.cell && ht.dead.includes("超时") && ht.dot, JSON.stringify(ht));

    // 英文模式：新词条端到端（orch.lang=en + 重载后 t()/data-i18n 全走英文）
    await evalJs(`localStorage.setItem("orch.lang", "en"); location.reload()`);
    await sleep(3500);
    await evalJs(`openRun("rtime1")`);
    await sleep(1200);
    const en = JSON.parse(await evalJs(`JSON.stringify({
      chip: (document.querySelector("#rd-steps .chip.timeout") || {}).textContent || "",
      edit: (document.getElementById("btn-editretry") || {}).textContent || "",
      badge: !!document.querySelector("#rd-meta .err-tag") })`));
    check("英文模式：超时芯片显示 Timeout", en.chip === "Timeout", JSON.stringify(en));
    check("英文模式：编辑重试按钮显示 Edit & retry",
      en.edit.includes("Edit & retry"), JSON.stringify(en));
    check("英文模式：TIMEOUT 徽标仍在（语言中立）", en.badge, JSON.stringify(en));
    // 英文模式：连载未达标的重写入口（rserial1）
    await evalJs(`openRun("rserial1")`);
    await sleep(1200);
    const enRw = JSON.parse(await evalJs(`JSON.stringify({
      retry: (document.getElementById("btn-retry") || {}).textContent || "" })`));
    check("英文模式：重写未达标章按钮显示 Rewrite unqualified chapters",
      enRw.retry.includes("Rewrite unqualified"), JSON.stringify(enRw));
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { if (svc) svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
