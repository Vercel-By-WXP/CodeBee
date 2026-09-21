/* 连载章节评审卡（renderChapterScores，含 event_check 逐项核对展示）浏览器内单测。
 * 直接以假 run 调渲染函数断言 DOM——不造任务/不起编排，稳且快。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 19100 + (process.pid % 300);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9700 + (process.pid % 400);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-cscard-"));
  writeFileSync(join(dataDir, "last-prefs.json"), JSON.stringify({
    type: "serial_novel", mode: "expert", thinking: "high"
  }), "utf8");
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-cs-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) {
        throw new Error(r.result.exceptionDetails.exception?.description ||
          r.result.exceptionDetails.text || "browser evaluation failed");
      }
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    const restoredPrefs = JSON.parse(await evalJs(`JSON.stringify({
      type: document.getElementById("f-type").value,
      mode: document.getElementById("f-mode").value,
      thinking: document.getElementById("f-thinking").value
    })`));
    check("新建任务恢复上次类型、模式与思考程度",
      restoredPrefs.type === "serial_novel" && restoredPrefs.mode === "expert" && restoredPrefs.thinking === "high",
      JSON.stringify(restoredPrefs));

    // 1) 无 chapter_scores 的 run → 卡片隐藏
    const hidden0 = await evalJs(`renderChapterScores({}); document.getElementById("rd-chapters").classList.contains("hidden")`);
    check("非连载 run 卡片隐藏", hidden0 === true);

    // 2) 带评分+逐项核对的连载 run → 渲染齐全
    const out = JSON.parse(await evalJs(`
      (() => {
        renderChapterScores({ chapter_scores: [
          { chapter: 3, title: "玄铁令", means: { "情节": 8.2, "人物": 7.1 }, passed: true, rounds: 2, words: 2600,
            event_check: ["1. 已完成：主角拿到玄铁令（第3段）", "2. 未完成：追兵未出现", "3. 待核实：伤势是否愈合"] },
          { chapter: 4, title: "比武", means: { "情节": 6.2 }, passed: false, rounds: 2, words: 1800 }
        ] });
        const box = document.getElementById("rd-chapters");
        return JSON.stringify({
          visible: !box.classList.contains("hidden"),
          text: box.textContent,
          nRows: box.querySelectorAll(".cs-row").length,
          nFail: box.querySelectorAll(".cs-row.fail").length,
          nOk: box.querySelectorAll(".cs-ev.ok").length,
          nBad: box.querySelectorAll(".cs-ev.bad").length,
          dims: box.querySelectorAll(".cs-dim").length,
          lowDims: box.querySelectorAll(".cs-dim.low").length,
          head: box.querySelector(".sec-title .tag").textContent
        });
      })()`));
    check("卡片渲染两章", out.visible && out.nRows === 2, JSON.stringify(out));
    check("达标计数徽标 1/2", out.head.replace(/\s/g, "").includes("1/2"), out.head);
    check("未达标章红框标记", out.nFail === 1);
    check("逐项核对行渲染（1 绿 1 红 1 灰）", out.nOk === 1 && out.nBad === 1, JSON.stringify(out));
    check("维度徽标渲染（低分标红）", out.dims === 3 && out.lowDims === 1);
    check("正文含核对文案与章题", out.text.includes("逐项目标核对") && out.text.includes("玄铁令"), out.text.slice(0, 80));

    // 3) 用户通常从侧栏进入任务级详情；章节卡不能只在单次运行详情可见
    const taskDetail = await evalJs(`
      (() => {
        const original = window.renderChapterScores;
        let calls = 0;
        window.renderChapterScores = (run) => { calls++; original(run); };
        S.taskSig = "";
        S.detailTaskKey = "task-serial";
        S.state = { tasks: [{ id: "task-serial", serial: { chapters: 2 }, status: "done" }], runs: [] };
        drawTaskDetail("task-serial", [{
          id: "run-serial", task_id: "task-serial", title: "连载任务", status: "done",
          created_at: "2026-09-21 16:00:00", steps: [], messages: [], chapter_scores: [
            { chapter: 1, title: "开篇", means: { "情节": 8 }, passed: true, words: 2200 }
          ]
        }]);
        window.renderChapterScores = original;
        return JSON.stringify({ calls, visible: !document.getElementById("rd-chapters").classList.contains("hidden") });
      })()`);
    const taskDetailResult = JSON.parse(taskDetail);
    check("任务级详情同样渲染章节评审卡", taskDetailResult.calls === 1 && taskDetailResult.visible,
      JSON.stringify(taskDetailResult));

    // 4) 英文态词条不空（切语言重渲染）
    const en = await evalJs(`
      (() => {
        window.setLang && window.setLang("en");
        renderChapterScores({ chapter_scores: [{ chapter: 1, title: "T", means: { "plot": 8 }, passed: true, words: 100, event_check: ["1. done"] }] });
        const txt = document.getElementById("rd-chapters").textContent;
        window.setLang && window.setLang("zh");
        return JSON.stringify({ has: txt.includes("Beat-by-beat check") && txt.includes("Chapter Reviews") });
      })()`);
    check("英文词条渲染", en && JSON.parse(en).has === true, en);
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }

  const fails = results.filter((r) => !r.ok);
  console.log(fails.length ? `\n${fails.length}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
