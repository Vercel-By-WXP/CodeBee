/* 侧栏「任务不消失」回归：单个任务刷满最近 40 条 run 窗口时，其余任务不得从侧栏消失。
 * 覆盖：4 个任务行全在（刷屏任务/窗口外任务/两个从未运行的任务）、活跃任务排前、
 *       从未运行的任务显示「暂无运行记录」、重绘后展开状态保留。
 * 前置：python tests/_seed_side_all_fixtures.py 造数 → TUTTI_DATA 指向它起临时服务
 *       （默认 http://127.0.0.1:18799，可用 SERVICE 环境变量覆盖）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SERVICE = process.env.SERVICE || "http://127.0.0.1:18799";
const CDP_PORT = 9340;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-sideall-"));
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
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- A) 刷屏任务占满 run 窗口，其余任务仍在侧栏 ---- */
    const og = JSON.parse(await evalJs(`JSON.stringify((() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      return {
        n: rows.length,
        keys: rows.map((d) => d.dataset.key),
        titles: rows.map((d) => (d.querySelector(".t") || {}).textContent || ""),
        firstKey: rows[0] && rows[0].dataset.key,
        firstHasMore: rows[0] && /查看全部/.test((rows[0].querySelector(".smore") || {}).textContent || ""),
        firstStepShown: rows[0] ? (rows[0].querySelector(".stepx .ssum") || {}).textContent || "" : null,
        norunMarked: rows.filter((d) => /暂无运行记录/.test(d.textContent)).length,
        norunClickable: rows.filter((d) => /暂无运行记录/.test(d.textContent))
          .map((d) => !!d.querySelector(".smore[onclick]")),
        quietStep: (() => { const q = rows.find((d) => d.dataset.key === "task-quiet");
          return q ? (q.querySelector(".ssum") || {}).textContent || "" : null; })(),
        runCover: (S.state.runs || []).slice(0, 40)
          .filter((r) => r.task_id === "task-flood").length,
      };
    })())`));
    check("后端最近 40 条 run 确实全被刷屏任务占据（场景成立）", og.runCover === 40, JSON.stringify(og.runCover));
    check("4 个任务全部出现在侧栏", og.n === 4, JSON.stringify({ n: og.n, keys: og.keys }));
    check("刷屏任务排第一（最近活动优先）", og.firstKey === "task-flood", og.firstKey);
    check("窗口外任务（5 小时前）在侧栏", og.keys.includes("task-quiet"), JSON.stringify(og.keys));
    check("窗口外任务显示的是它自己的真实步骤（task_latest 兜底）",
      og.quietStep && og.quietStep.includes("早就结束的运行"), og.quietStep);
    check("从未运行的两个任务也在侧栏",
      og.keys.includes("task-norun1") && og.keys.includes("task-norun2"), JSON.stringify(og.keys));
    check("刷屏任务显示最近一次运行的真实步骤（不再跨 run 累加）",
      (og.firstStepShown || "").includes("第 1 次运行"), og.firstStepShown);
    check("从未运行的任务标「暂无运行记录」且不可点", og.norunMarked === 2 && og.norunClickable.every((c) => !c),
      JSON.stringify({ marked: og.norunMarked, clickable: og.norunClickable }));

    /* ---- B) 展开状态跨重绘保留（顺手验证兜底分组不打断原有机制） ---- */
    await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      rows[2].open = true;
      S.sideSig = null;
      renderSideTasks();
      return "ok";
    })()`);
    await sleep(400);
    const kept = JSON.parse(await evalJs(`JSON.stringify((() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      return { n: rows.length, third: rows[2] && rows[2].hasAttribute("open") };
    })())`));
    check("强制重绘后行数不缩水、展开状态保留", kept.n === 4 && kept.third, JSON.stringify(kept));

    const ok = results.length && results.every(Boolean);
    console.log(ok ? "\n全部通过" : "\n有失败项");
    process.exitCode = ok ? 0 : 1;
  } finally {
    try { proc.kill(); } catch (e) { /* 已退出 */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* 下次清理 */ }
  }
}

main().catch((e) => { console.error("脚本失败：", e.message); process.exit(1); });
