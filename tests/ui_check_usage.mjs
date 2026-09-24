/* 用量统计页 UI 验证：自起临时服务（预置台账种子）→ Edge headless + CDP
 * 打开用量页 → 断言 KPI/趋势图/维度排行/最近调用渲染 → 切换时间范围 → 截图。
 * 结束清理浏览器与服务进程、临时目录。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18796;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9337;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PY = process.execPath.includes("python") ? "python" : "python";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function isoDay(offset = 0) {
  const d = new Date(Date.now() + offset * 86400000);
  return d.toISOString().slice(0, 10);
}

function seedUsage(dataDir) {
  const ym = isoDay().slice(0, 7).replace("-", "");
  const rec = (day, over) => JSON.stringify(Object.assign({
    ts: day + " 12:00:00", day, source: "pipeline", run_id: "r-ui", task_id: "t-ui",
    task_type: "code", role: "implement", agent: "codex-cli", agent_label: "Codex CLI",
    tool: "codex", model: "gpt-ui", provider: "", ok: true, duration_s: 5,
    cost_usd: 0.02, input: 1000, output: 400, cached: 200, reasoning: 0, total: 1600,
  }, over));
  const lines = [
    rec(isoDay(0), { first_token_ms: 640, tokens_per_sec: 40 }),
    rec(isoDay(-1), { tool: "claude", agent: "claude-code", model: "claude-ui", role: "review", output: 900, total: 2300, first_token_ms: 1200, tokens_per_sec: 60 }),
    rec(isoDay(-2), { tool: "orchestrator", agent: "orchestrator", agent_label: "编排者", model: "orch-ui", role: "plan", input: 300, output: 100, total: 400 }),
    rec(isoDay(-5), { tool: "claude", agent: "claude-code", model: "claude-ui", role: "draft", ok: false, total: 1500, input: 1100, output: 400 }),
    rec(isoDay(-40), { tool: "qwen", model: "qwen-ui", total: 5000, input: 4000, output: 1000 }),
  ].join("\n") + "\n";
  mkdirSync(join(dataDir, "usage"), { recursive: true });
  writeFileSync(join(dataDir, "usage", "usage-" + ym + ".jsonl"), lines, "utf8");
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uiusage-"));
  const dataDir = join(tmp, "data");
  seedUsage(dataDir);

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    cwd: ROOT, stdio: "ignore",
    env: Object.assign({}, process.env, { TUTTI_DATA: dataDir, PYTHONPATH: ROOT }),
  });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    if (!target) throw new Error("no CDP target");

    ws = new WebSocket(target.webSocketDebuggerUrl);
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
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
      return r.result?.result?.value;
    };
    const shot = (name) => send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", name), Buffer.from(r.result.data, "base64"));
    });

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3000);

    // 1) 导航进入用量页（默认范围=今天）
    await evalJs(`localStorage.removeItem("orch.usageDays"); S.usageDays = undefined; switchTab("usage"); "ok"`);
    await sleep(1400);
    const visible = await evalJs(`!document.getElementById("sub-usage").classList.contains("hidden")`);
    check("用量页可见", visible === true);
    const title = await evalJs(`document.getElementById("page-title").textContent`);
    check("页标题=用量统计", title === "用量统计", title);

    // 2) 默认选中「今天」：只有当天 1 条（1600）
    const defActive = await evalJs(`[...document.querySelectorAll("#usage-ranges [data-days]")]
      .filter(b => b.classList.contains("active")).map(b => b.dataset.days).join(",")`);
    check("默认选中「今天」", defActive === "1", "active=" + defActive);
    const todayKpi = await evalJs(`document.getElementById("usage-kpis").textContent`);
    check("默认（今天）tokens=1600", /1,?600|1600/.test(todayKpi), todayKpi.slice(0, 120));

    // 3) 显式切「近 30 天」：4 条（40 天前那条被排除）
    await evalJs(`setUsageDays(30); "ok"`);
    await sleep(1200);
    const kpiText = await evalJs(`document.getElementById("usage-kpis").textContent`);
    check("KPI 总 tokens=5800（1600+2300+400+1500）", /5,?800|5800/.test(kpiText), kpiText.slice(0, 120));
    check("KPI 副行调用 4 次", /调用\s*4/.test(kpiText), kpiText.slice(0, 160));
    check("KPI 副行成功率 75%（4 中 3 成）", /成功率\s*75/.test(kpiText), kpiText.slice(0, 160));

    // 3b) 体验指标卡：只有 2 条可测记录参与均值（不可测的两条不得把首字拉成 0）
    check("首字延迟卡渲染", /首字延迟/.test(kpiText), kpiText.slice(0, 200));
    check("首字延迟=920ms（640+1200 的均值，仅可测样本）", /920ms/.test(kpiText), kpiText.slice(0, 200));
    check("吞吐=50 tok/s 且样本 2 次", /50\s*tok\/s/.test(kpiText) && /样本\s*2/.test(kpiText),
      kpiText.slice(0, 240));
    await evalJs(`setUsageDays(1); "ok"`);
    await sleep(1200);
    const todayPerf = await evalJs(`document.getElementById("usage-kpis").textContent`);
    check("切回今天=640ms", /640ms/.test(todayPerf), todayPerf.slice(0, 200));
    await evalJs(`setUsageDays(30); "ok"`);
    await sleep(1200);

    // 4) 趋势图 SVG（多模型折线：≥2 天走 path 曲线 + 数据点）与维度排行
    const pathN = await evalJs(`document.querySelectorAll("#usage-trend svg path").length`);
    check("趋势图 SVG 有折线", pathN >= 1, "paths=" + pathN);
    const dotN = await evalJs(`document.querySelectorAll("#usage-trend svg circle").length`);
    check("趋势图有数据点", dotN >= 4, "circles=" + dotN);
    const dimsHtml = await evalJs(`document.getElementById("usage-dims").textContent`);
    check("维度表含工具/智能体/模型/角色/任务类型", ["按工具", "按智能体", "按模型", "按步骤角色", "按任务类型"]
      .every((k) => dimsHtml.includes(k)), dimsHtml.slice(0, 120));
    check("工具维度含编排者 API", dimsHtml.includes("编排者 · 直连API"), "");
    check("最近调用表渲染", /最近调用/.test(await evalJs(`document.body.textContent`)));
    const recentRows = await evalJs(`document.querySelectorAll("#usage-recent tr").length`);
    check("最近调用行数 ≥4", recentRows >= 4, "rows=" + recentRows);
    await shot("usage-30d.png");

    // 5) 切换「全部」→ 40 天前的 qwen 记录出现
    await evalJs(`setUsageDays(0); "ok"`);
    await sleep(1200);
    const allText = await evalJs(`document.getElementById("usage-kpis").textContent`);
    check("切全部后 tokens=10800（显示 1.1万）", /10,?800|1\.1万/.test(allText), allText.slice(0, 120));
    const dimsAll = await evalJs(`document.getElementById("usage-dims").textContent`);
    check("全部范围出现 Qwen CLI", dimsAll.includes("Qwen CLI"), "");
    await shot("usage-all.png");

    // 6) 切「今天」→ 只剩当天 1600
    await evalJs(`setUsageDays(1); "ok"`);
    await sleep(1200);
    const todayText = await evalJs(`document.getElementById("usage-kpis").textContent`);
    check("切今天后 tokens=1600", /1,?600|1600/.test(todayText), todayText.slice(0, 120));
    await shot("usage-today.png");

    // 7) 服务端无异常
    const st = await fetch(SERVICE + "/api/usage?days=7").then((r) => r.status);
    check("服务端 /api/usage 200", st === 200);

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) {
      if (p && p.pid) {
        try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
      }
    }
    if (results.some((r) => !r.ok)) {
      console.log("（失败现场保留：%s）", tmp);
    } else {
      try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 用量页 UI/CDP：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
