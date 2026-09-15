/* 侧栏「任务不消失」回归：单个任务刷满最近 40 条 run 窗口时，其余任务不得从侧栏消失。
 * 树形 + 紧凑时间式样：任务行（字形/省略标题/时间）→ 二级步骤行。
 * 覆盖：4 个任务行全在（刷屏任务/窗口外任务/两个从未运行的任务）、活跃任务排前、
 *       步骤与「查看全部」计数来自后端全量统计、点步骤开运行详情。
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
    // 负载高时首轮轮询 3.5s 内完不成，行还没渲染：等行出现再断言（不改断言语义）
    for (let i = 0; i < 20; i++) {
      const n = await evalJs(`document.querySelectorAll("#side-tasks .stask").length`);
      if (n > 0) break;
      await sleep(500);
    }

    /* ---- A) 刷屏任务占满 run 窗口，其余任务仍在侧栏（树形 + 紧凑时间式样） ---- */
    const og = JSON.parse(await evalJs(`JSON.stringify((() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      const glyphOf = (d) => [...d.querySelector("summary .sglyph").classList].filter((c) => c !== "sglyph").join(" ");
      const firstSmore = [...(rows[0] || {}).querySelectorAll(".smore")].map((x) => x.textContent.trim());
      return {
        n: rows.length,
        keys: rows.map((d) => d.dataset.key),
        firstKey: rows[0] && rows[0].dataset.key,
        firstGlyph: rows[0] ? glyphOf(rows[0]) : "",
        firstMore: firstSmore,
        firstStep: rows[0] ? ((rows[0].querySelector(".stepx .ssum") || {}).textContent || "") : "",
        quietStep: (() => { const q = rows.find((d) => d.dataset.key === "task-quiet");
          return q ? ((q.querySelector(".stepx .ssum") || {}).textContent || "") : null; })(),
        quietGlyph: (() => { const q = rows.find((d) => d.dataset.key === "task-quiet");
          return q ? glyphOf(q) : null; })(),
        norun: rows.filter((d) => ["task-norun1", "task-norun2"].includes(d.dataset.key))
          .map((d) => ({ g: glyphOf(d), text: d.querySelector(".smore")?.textContent.trim() || "",
            clickable: !!d.querySelector(".smore[onclick]") })),
        runCover: (S.state.runs || []).slice(0, 40)
          .filter((r) => r.task_id === "task-flood").length,
      };
    })())`));
    check("后端最近 40 条 run 确实全被刷屏任务占据（场景成立）", og.runCover === 40, JSON.stringify(og.runCover));
    check("4 个任务全部出现在侧栏", og.n === 4, JSON.stringify({ n: og.n, keys: og.keys }));
    check("刷屏任务排第一（最近活动优先）", og.firstKey === "task-flood", og.firstKey);
    check("刷屏任务行内是它最近一次运行的真实步骤",
      (og.firstStep || "").includes("第 1 次运行"), og.firstStep);
    check("「查看全部」计数用后端全量统计（45 次运行，不是窗口的 40）且辅助行唯一",
      og.firstMore.length === 1 && /查看全部 45 次运行 · 45 步/.test(og.firstMore[0]),
      JSON.stringify(og.firstMore));
    check("窗口外任务（5 小时前）在侧栏：完成绿点 + 显示自己的真实步骤",
      og.keys.includes("task-quiet") && og.quietGlyph === "ok" &&
      (og.quietStep || "").includes("早就结束的运行"),
      JSON.stringify({ keys: og.keys, quietGlyph: og.quietGlyph, quietStep: og.quietStep }));
    check("从未运行的两个任务在侧栏：暗点 +「暂无运行记录」不可点",
      og.norun.length === 2 && og.norun.every((x) => x.g === "none" &&
        x.text === "暂无运行记录" && !x.clickable), JSON.stringify(og.norun));

    /* ---- B) 点窗口外任务的步骤 → 主栏打开它自己的运行详情 ---- */
    await evalJs(`(() => {
      const rows = [...document.querySelectorAll("#side-tasks .stask")];
      const q = rows.find((d) => d.dataset.key === "task-quiet");
      q.open = true;
      q.querySelector(".stepx").click();
      S.sideSig = null;
      renderSideTasks();
      return "ok";
    })()`);
    await sleep(1000);
    const opened = JSON.parse(await evalJs(`JSON.stringify((() => {
      const act = document.querySelector("#side-tasks .stask.active");
      return {
        title: document.getElementById("page-title").textContent,
        detailShown: !document.getElementById("run-detail").classList.contains("hidden"),
        activeKey: act ? act.dataset.key : ""
      };
    })())`));
    check("点窗口外任务的步骤打开运行详情且任务行高亮", opened.title === "运行详情" &&
      opened.detailShown && opened.activeKey === "task-quiet", JSON.stringify(opened));

    const ok = results.length && results.every(Boolean);
    console.log(ok ? "\n全部通过" : "\n有失败项");
    process.exitCode = ok ? 0 : 1;
  } finally {
    try { proc.kill(); } catch (e) { /* 已退出 */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* 下次清理 */ }
  }
}

main().catch((e) => { console.error("脚本失败：", e.message); process.exit(1); });
