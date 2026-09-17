/* 作品信息一键生成验收（独立 TAB 版）：Edge headless + CDP，零依赖。
 * 造数：连载任务（done run + 章节 + 圣经 + 大纲）→
 * 断言：第 6 个 TAB（作品信息）可用且终态不抢自动选卡 / 引导占位 /
 * 点生成（服务端模板兜底，确定性）→ 字段内联逐条 + 复制按钮 + toast /
 * TAB 徽章 1/2 → 2/2 / 重新生成 / 番茄14字段/七猫12字段。
 * 临时数据目录 + 独立端口（18834 / CDP 9367，与其他 UI 测试互不冲撞）。
 * 用法：node tests/ui_bookmeta.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18834;
const CDP_PORT = 9367;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990103-000000-0004";
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-bmui-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(runsDir, "steps", "01-outline-orch.log"), "--- 输出 ---\n大纲 10 章\n", "utf-8");
  writeFileSync(join(workdir, "chapter-001.md"), "# 第一章\n\n开局即冲突。\n", "utf-8");
  writeFileSync(join(workdir, "story-bible.md"), "# 故事圣经\n\n女主：沈青梧。\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", "task-bmui.json"), JSON.stringify({
    id: "task-bmui", title: "宅斗长篇连载", type: "serial_novel",
    goal: "女频古代宅斗长篇：庶女翻身执掌家宅", workdir, status: "done",
    serial: { chapters: 10, words_per_chapter: 2000, start_chapter: 1 },
    created_at: "2000-01-03 00:00:00", mode: "manual", archived: false,
  }, null, 2));
  // 续写批次（start_chapter > 1）：不该出「作品信息」TAB——开书资料属于这本书
  writeFileSync(join(dataDir, "tasks", "task-bmcont.json"), JSON.stringify({
    id: "task-bmcont", title: "宅斗长篇连载·续写", type: "serial_novel",
    goal: "接着写", workdir, status: "done",
    serial: { chapters: 8, words_per_chapter: 2000, start_chapter: 11, continues: "task-bmui" },
    created_at: "2000-01-03 00:10:00", mode: "manual", archived: false,
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "宅斗长篇连载",
    task_id: "task-bmui", status: "done", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "orch", agent_label: "编排者", note: "", status: "done",
        started_at: "00:00:01", ended_at: "00:00:20", duration_s: 19, exit_code: 0,
        summary: "大纲来源 编排者：10 章", log: "steps/01-outline-orch.log", cost_usd: 0, tokens: 0 },
    ],
    outline: { book_title: "《金枝》", chapters: [
      { title: "开局", beats: "庶女被算计，绝地反击", hook: "夜里来了个陌生人" },
    ] },
    created_at: "2000-01-03 00:00:00", started_at: "2000-01-03 00:00:00",
    ended_at: "2000-01-03 00:02:00", cost_usd: 0, tokens: 0, error: "",
    verdict: null, summary: "",
  }, null, 2));
  // 续写批次的 run（详情页要能打开，才能验它不出作品信息 TAB）
  const contRunId = "r-20990103-000000-0005";
  const contRunsDir = join(dataDir, "runs", contRunId);
  mkdirSync(join(contRunsDir, "steps"), { recursive: true });
  writeFileSync(join(contRunsDir, "steps", "01-outline-orch.log"), "--- 输出 ---\n续写大纲\n", "utf-8");
  writeFileSync(join(contRunsDir, "run.json"), JSON.stringify({
    id: contRunId, kind: "orchestration", title: "宅斗长篇连载·续写",
    task_id: "task-bmcont", status: "done", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "orch", agent_label: "编排者", note: "", status: "done",
        started_at: "00:10:01", ended_at: "00:10:20", duration_s: 19, exit_code: 0,
        summary: "续写大纲 8 章", log: "steps/01-outline-orch.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2000-01-03 00:10:00", started_at: "2000-01-03 00:10:00",
    ended_at: "2000-01-03 00:12:00", cost_usd: 0, tokens: 0, error: "",
    verdict: null, summary: "",
  }, null, 2));

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
    check("临时服务就绪(18834)", up);

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

    // A) 打开任务级详情：作品信息 TAB 在场；终态自动选卡仍落「成果」（不抢）
    const tabs = await evalJson(`(async () => {
      sideOpenTask("task-bmui");
      await new Promise((r) => setTimeout(r, 1200));
      const tb = Array.from(document.querySelectorAll("#rd-tabs .rd-tab"));
      return {
        ids: tb.map((b) => b.dataset.tab).join(","),
        hiddenIds: tb.filter((b) => b.classList.contains("hidden")).map((b) => b.dataset.tab).join(","),
        active: ((document.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || "",
      };
    })()`);
    check("TAB 条含作品信息", tabs.ids.includes("bookmeta"), tabs.ids);
    check("无 git → 版本隐藏，作品信息在场",
      tabs.hiddenIds.split(",").includes("git") && !tabs.hiddenIds.split(",").includes("bookmeta"),
      tabs.hiddenIds);
    check("终态自动选卡仍落「成果」（作品信息不抢）", tabs.active === "result", tabs.active);

    // A2) 续写批次（start_chapter=11）：作品信息 TAB 不出现——开书资料属于这本书
    const cont = await evalJson(`(async () => {
      sideOpenTask("task-bmcont");
      await new Promise((r) => setTimeout(r, 1200));
      const tb = Array.from(document.querySelectorAll("#rd-tabs .rd-tab"));
      return {
        ids: tb.filter((b) => !b.classList.contains("hidden")).map((b) => b.dataset.tab).join(","),
        hasBm: tb.some((b) => b.dataset.tab === "bookmeta" && !b.classList.contains("hidden")),
        panelHidden: document.getElementById("rd-bookmeta").classList.contains("hidden"),
      };
    })()`);
    check("续写批次不出「作品信息」TAB", cont.hasBm === false && cont.panelHidden === true,
      JSON.stringify(cont));
    check("续写批次其余分区照旧（蜂巢/步骤/成果/圣经都在）",
      ["hive", "steps", "result", "bible"].every((k) => cont.ids.split(",").includes(k)),
      cont.ids);

    // 回到首批任务，后续段落继续验作品信息面板
    await evalJson(`(async () => { sideOpenTask("task-bmui");
      await new Promise((r) => setTimeout(r, 1000)); return "ok"; })()`);

    // B) 点 TAB：引导占位 + 番茄/七猫两个生成按钮
    const panel = await evalJson(`(async () => {
      document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]').click();
      await new Promise((r) => setTimeout(r, 300));
      const box = document.getElementById("rd-bookmeta");
      return {
        paneVisible: !document.querySelector('.rd-pane[data-pane="bookmeta"]').classList.contains("hidden"),
        guide: !!document.querySelector("#rd-bookmeta .bm-guide"),
        genBtns: document.querySelectorAll("#rd-bookmeta .bm-card button.primary").length,
        text: box ? box.textContent : "",
      };
    })()`);
    check("点 TAB 切到作品信息分区", panel.paneVisible, JSON.stringify(panel).slice(0, 120));
    check("未生成时引导占位在场", panel.guide, panel.text.slice(0, 120));
    check("番茄/七猫两个生成按钮", panel.genBtns === 2, panel.genBtns);

    // C) 点番茄生成（服务端模板兜底，确定性）→ 字段内联出现
    // 状态链路：后台线程秒级完成，但 SSE→前端轮询刷新要两跳（实测 ~6-11s，
    // 并行测试抢 CPU 时可能拖到 ~70s），等待窗口给足 120s
    const clicked = await evalJson(`(() => {
      const btn = document.querySelector("#rd-bookmeta .bm-card button.primary");
      if (!btn) return null;
      btn.click();
      return btn.textContent.trim();
    })()`);
    check("点番茄「生成」按钮", clicked === "生成", clicked);
    let fanqie = null;
    for (let i = 0; i < 120 && !fanqie; i++) {
      await sleep(1000);
      fanqie = await evalJson(`(() => {
        const card = document.querySelector("#rd-bookmeta .bm-card.st-done");
        if (!card) return null;
        const rows = card.querySelectorAll(".bm-f").length;
        const copies = card.querySelectorAll(".bm-copy").length;
        const badge = (document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"] .rd-badge') || {}).textContent || "";
        return { rows, copies, badge };
      })()`);
    }
    // 番茄字段 2026-09 扩到 10 个（分类 + 主题/角色/情节 三组标签，见 bookmeta.FIELD_LABELS）
    check("番茄生成完成：14 字段内联 + 每条复制按钮",
      fanqie && fanqie.rows === 14 && fanqie.copies === 14, JSON.stringify(fanqie) +
      (fanqie ? "" : "　panel=" + (await evalJson(`(document.getElementById("rd-bookmeta") || {}).textContent || "NOBOX"`))));
    check("TAB 徽章 1/2", fanqie && fanqie.badge === "1/2", fanqie && fanqie.badge);

    // D) 复制按钮 → toast
    const copyToast = await evalJson(`(async () => {
      const btn = document.querySelector("#rd-bookmeta .bm-card.st-done .bm-copy");
      if (!btn) return null;
      btn.click();
      await new Promise((r) => setTimeout(r, 300));
      const toast = document.querySelector("#toast");
      return { toastText: toast ? toast.textContent : "" };
    })()`);
    check("点复制出 toast（已复制：作品名）", copyToast &&
      (copyToast.toastText || "").includes("已复制"), copyToast && copyToast.toastText);

    // E) 七猫生成 → 9 字段；徽章 2/2；重新生成 ×2（按平台名精确匹配卡片）
    await evalJson(`(() => {
      const names = Array.from(document.querySelectorAll("#rd-bookmeta .bm-plat-name"));
      const qm = names.find((n) => n.textContent.trim() === "七猫");
      const gen = qm && qm.closest(".bm-card").querySelector("button.primary");
      if (gen) gen.click();
      return !!gen;
    })()`);
    let qimao = null;
    for (let i = 0; i < 120 && !qimao; i++) {
      await sleep(1000);
      qimao = await evalJson(`(() => {
        const names = Array.from(document.querySelectorAll("#rd-bookmeta .bm-plat-name"));
        const qm = names.find((n) => n.textContent.trim() === "七猫");
        if (!qm) return null;
        const card = qm.closest(".bm-card");
        if (!card.classList.contains("st-done")) return null;
        const badge = (document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"] .rd-badge') || {}).textContent || "";
        const labels = Array.from(card.querySelectorAll(".bm-f-k")).map((x) => x.textContent);
        return { rows: card.querySelectorAll(".bm-f").length, badge,
          hasCategory: labels.some((x) => x.includes("一级分类")),
          hasStatus: labels.some((x) => x.includes("作品状态")) };
      })()`);
    }
    check("七猫生成完成：12 字段（四组标签/分类/状态）", qimao && qimao.rows === 12 &&
      qimao.hasCategory && qimao.hasStatus, JSON.stringify(qimao));
    check("TAB 徽章 2/2", qimao && qimao.badge === "2/2", qimao && qimao.badge);
    const regen = await evalJson(`(() =>
      Array.from(document.querySelectorAll("#rd-bookmeta .bm-card button"))
        .filter((b) => b.textContent.includes("重新生成")).length)()`);
    check("done 态出现「重新生成」×2", regen === 2, regen);

    check("无控制台错误", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));
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
