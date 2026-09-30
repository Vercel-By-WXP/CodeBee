/* 作品信息面板跨任务串台回归（2026-09-30 用户实案：A 任务详情里点开
 * 「发一章」章节清单后切到 B 任务，TASK DETAIL 标题已换，作品信息页签
 * 仍挂着 A 的建书/发布数据且不自愈——展开态守卫不分「同任务重绘」和
 * 「切换任务」）。修法：#rd-bookmeta 记 data-task-id，守卫只保护同一任务。
 *
 * Edge headless + CDP，零依赖。断言：
 *  1) A 详情：番茄卡显示「已建书：甲书实录」，点「发一章」展开章节清单；
 *  2) 切到 B 详情：标题换成 B，面板 data-task-id=B，A 的发布数据退场、
 *     出现 B 的「登记已有作品」（跨任务放行重绘）；
 *  3) 同任务守卫仍在：B 面板登记输入聚焦时手动触发重绘，元素不重建、值不丢；
 *  4) 切回 A：「已建书：甲书实录」回归（双向都跟手）；
 *  5) 全程无控制台错误。
 * 临时数据目录 + 独立端口（18887 / CDP 9413，与其他 UI 测试互不冲撞）。
 * 用法：node tests/ui_bookmeta_crosstalk.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = Number(process.env.TUTTI_TEST_PORT) || 18887;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9413;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const TASK_A = "task-bmxt-a";
const TASK_B = "task-bmxt-b";
const RUN_A = "r-20990106-000000-0001";
const RUN_B = "r-20990106-000000-0002";
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

function seedTask(id, title, workdir) {
  return {
    id, title, type: "serial_novel", goal: "长篇连载", workdir,
    status: "done", mode: "manual", archived: false,
    serial: { chapters: 6, words_per_chapter: 2000, start_chapter: 1 },
    created_at: "2000-01-06 00:00:00",
  };
}
function seedRun(id, title, taskId) {
  return {
    id, kind: "orchestration", title, task_id: taskId, status: "done", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "orch", agent_label: "编排者", note: "", status: "done",
        started_at: "00:00:01", ended_at: "00:00:20", duration_s: 19, exit_code: 0,
        summary: "大纲 6 章", log: "", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2000-01-06 00:00:00", started_at: "2000-01-06 00:00:00",
    ended_at: "2000-01-06 00:02:00", cost_usd: 0, tokens: 0, error: "",
    verdict: null, summary: "",
  };
}

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-bmxstalk-"));
  const workA = join(dataDir, "book-a");
  const workB = join(dataDir, "book-b");
  mkdirSync(workA, { recursive: true });
  mkdirSync(workB, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "publish"), { recursive: true });
  for (const [rid, tid, title] of [
    [RUN_A, TASK_A, "甲书运行标题实测"], [RUN_B, TASK_B, "乙书运行标题实测"]]) {
    const rdir = join(dataDir, "runs", rid);
    mkdirSync(rdir, { recursive: true });
    writeFileSync(join(rdir, "run.json"), JSON.stringify(seedRun(rid, title, tid), null, 2), "utf-8");
  }
  writeFileSync(join(dataDir, "tasks", `${TASK_A}.json`), JSON.stringify(
    seedTask(TASK_A, "甲书连载实测", workA), null, 2), "utf-8");
  writeFileSync(join(dataDir, "tasks", `${TASK_B}.json`), JSON.stringify(
    seedTask(TASK_B, "乙书连载实测", workB), null, 2), "utf-8");
  // A 已建书（bookReady → 有「发一章」无「登记已有作品」），B 无绑定（反之）
  writeFileSync(join(dataDir, "publish", "books.json"), JSON.stringify({
    [TASK_A]: { fanqie: { book_id: "730001", title: "甲书实录",
      url: "", created_at: "2000-01-06 00:00:00" } },
  }, null, 2), "utf-8");
  writeFileSync(join(workA, "chapter-001.md"), "# 第一章\n\n开局即冲突。\n", "utf-8");
  writeFileSync(join(workA, "chapter-002.md"), "# 第二章\n\n冲突升级。\n", "utf-8");

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir, TUTTI_BOOKMETA_TEMPLATE_ONLY: "1" },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18887)", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
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
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };
    const openTaskBookmeta = (tid) => evalJson(`(async () => {
      sideOpenTask(${JSON.stringify(tid)});
      await new Promise((r) => setTimeout(r, 1200));
      const tab = document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]');
      if (tab && !tab.classList.contains("hidden")) tab.click();
      await new Promise((r) => setTimeout(r, 500));
      return "ok";
    })()`);
    const boxText = () => evalJson(
      `(() => { const b = document.getElementById("rd-bookmeta");
        return { text: (b && b.textContent || ""), tid: b && b.dataset ? b.dataset.taskId : undefined };
      })()`);

    // ── 1) A 详情：已建书 + 展开章节清单 ─────────────────────────────
    await openTaskBookmeta(TASK_A);
    let aView = null;
    for (let i = 0; i < 15 && !aView; i++) {
      await sleep(1000);
      aView = await evalJson(`(() => {
        const b = document.getElementById("rd-bookmeta");
        return { ready: !!b && (b.textContent || "").includes("已建书：甲书实录"),
          taskId: b && b.dataset ? b.dataset.taskId : undefined };
      })()`);
      if (aView && aView.ready) aView = { ready: true, taskId: aView.taskId };
      else aView = null;
    }
    check("A 详情：番茄卡显示「已建书：甲书实录」", !!aView,
      JSON.stringify(aView || (await boxText())));
    check("A 面板落 data-task-id 标记", aView && aView.taskId === TASK_A,
      aView && aView.taskId);

    const opened = await evalJson(`(() => {
      const btns = Array.from(document.querySelectorAll("#rd-bookmeta button"));
      const b = btns.find((x) => x.textContent.includes("发一章"));
      if (!b) return { found: false };
      b.click();
      return { found: true };
    })()`);
    let chaptersOpen = null;
    for (let i = 0; i < 10 && !chaptersOpen; i++) {
      await sleep(1000);
      chaptersOpen = await evalJson(
        `(() => !!document.querySelector("#rd-bookmeta .pb-chapters:not(.hidden) .pb-ch-item"))()`);
    }
    check("A 详情：点「发一章」章节清单展开（展开态就位）",
      opened.found === true && chaptersOpen === true,
      JSON.stringify({ opened, chaptersOpen }));

    // ── 2) 切到 B 详情：面板必须跟手换任务（修前残留 A 的数据） ──────
    await openTaskBookmeta(TASK_B);
    await sleep(1500);
    const bView = await evalJson(`(() => {
      const b = document.getElementById("rd-bookmeta");
      return { title: document.getElementById("rd-title").textContent,
        text: (b.textContent || ""), taskId: b.dataset ? b.dataset.taskId : undefined };
    })()`);
    check("B 详情：TASK DETAIL 标题换成乙书", bView.title === "乙书运行标题实测",
      bView.title);
    check("B 详情：面板 data-task-id=B（跨任务放行重绘）",
      bView.taskId === TASK_B, bView.taskId);
    check("B 详情：A 的「已建书：甲书实录」退场",
      !(bView.text || "").includes("已建书：甲书实录"), (bView.text || "").slice(0, 160));
    check("B 详情：出现 B 的「登记已有作品」入口",
      (bView.text || "").includes("登记已有作品"), (bView.text || "").slice(0, 160));

    // ── 3) 同任务守卫仍在：输入聚焦时重绘被跳过（元素不重建、值不丢） ──
    const guardOn = await evalJson(`(() => {
      const btns = Array.from(document.querySelectorAll("#rd-bookmeta button"));
      const b = btns.find((x) => x.textContent.includes("登记已有作品"));
      if (!b) return { err: "no reg button" };
      b.click();
      const input = document.getElementById("pb-reg-title-fanqie");
      if (!input) return { err: "no reg input" };
      input.focus();
      input.value = "乙书草稿名";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dataset.probe = "1";   // 重绘=innerHTML 重建=新元素无此标记
      if (typeof renderBookMetaPanel !== "function") return { err: "no global fn" };
      const tk = (S.state.tasks || []).find((x) => x.id === ${JSON.stringify(TASK_B)});
      renderBookMetaPanel(tk);
      const after = document.getElementById("pb-reg-title-fanqie");
      return { kept: after === input && after.dataset.probe === "1",
        val: after ? after.value : "(gone)" };
    })()`);
    check("同任务 + 输入聚焦：重绘被守卫跳过（元素 identity 与值都在）",
      guardOn && guardOn.kept === true && guardOn.val === "乙书草稿名",
      JSON.stringify(guardOn));

    // ── 4) 切回 A：已建书回归（双向跟手） ────────────────────────────
    await openTaskBookmeta(TASK_A);
    await sleep(1500);
    const backA = await boxText();
    check("切回 A 详情：番茄卡「已建书：甲书实录」回归",
      (backA.text || "").includes("已建书：甲书实录") && backA.tid === TASK_A,
      JSON.stringify(backA).slice(0, 200));

    check("无控制台错误", consoleErrors.length === 0,
      consoleErrors.join(" | ").slice(0, 300));
  } finally {
    try { edge.kill(); } catch (e) { /* noop */ }
    try { srv.kill(); } catch (e) { /* noop */ }
    await sleep(500);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* noop */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* noop */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n通过 %d / 失败 %d", results.length - bad.length, bad.length);
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error("测试崩溃:", e); process.exit(2); });
