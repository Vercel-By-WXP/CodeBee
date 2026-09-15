/* 使用统计新版 UI 验证：KPI 五卡 / Token 活动热力图（三粒度切换）/ 多模型折线 /
 * 模型用量甜甜圈 / 模型名大小写归一。自起临时服务（预置台账种子）→ Edge headless
 * + CDP 断言 → 截图，结束清理进程与临时目录。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18794;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9339;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function isoDay(offset = 0) {
  const d = new Date(Date.now() + offset * 86400000);
  return d.toISOString().slice(0, 10);
}

function seedUsage(dataDir) {
  // 覆盖点：大小写归一（GLM 两写法）、>5 模型（折线/甜甜圈出「其他」）、
  // 40 天前的老数据（热力图窗口应覆盖到而不只是最近 8 周）
  const rec = (day, over) => JSON.stringify(Object.assign({
    ts: day + " 12:00:00", day, source: "pipeline", run_id: "r-us", task_id: "t-us",
    task_type: "code", role: "implement", agent: "codex-cli", agent_label: "Codex CLI",
    tool: "codex", model: "GLM-5.3-Flash", provider: "", ok: true, duration_s: 30,
    cost_usd: 0.02, input: 500, output: 200, cached: 100, reasoning: 0, total: 800,
  }, over));
  const lines = [
    rec(isoDay(0), { total: 900, input: 600 }),
    rec(isoDay(0), { model: "glm-5.3-flash", tool: "claude", agent: "claude-code", total: 300 }),
    rec(isoDay(0), { model: "MiniMax-M3", total: 200 }),
    rec(isoDay(-1), { model: "DeepSeek-V4-Flash", total: 700 }),
    rec(isoDay(-1), { model: "[opencode]deepseek-v4-flash", tool: "opencode", total: 150 }),
    rec(isoDay(-3), { model: "qwen-ui", tool: "qwen", total: 120 }),
    rec(isoDay(-3), { model: "phi-ui", total: 80 }),
    rec(isoDay(-40), { model: "qwen-ui", tool: "qwen", total: 4000, input: 3000 }),
  ].join("\n") + "\n";
  mkdirSync(join(dataDir, "usage"), { recursive: true });
  writeFileSync(join(dataDir, "usage", "usage-" + isoDay().slice(0, 7).replace("-", "") + ".jsonl"), lines, "utf8");
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uistats-"));
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

    // 进入用量页（近 30 天：折线与甜甜圈信息最全）
    await evalJs(`localStorage.removeItem("orch.usageDays"); S.usageDays = undefined; switchTab("usage"); "ok"`);
    await sleep(1400);
    await evalJs(`setUsageDays(30); "ok"`);
    await sleep(1200);

    // 1) 页内标题与徽章
    const head = await evalJs(`document.querySelector("#sub-usage .panel-head h2").textContent`);
    check("页内标题=使用统计+应用用量徽章", head.includes("使用统计") && head.includes("应用用量"), head);

    // 2) KPI 五卡（截图口径）
    const kpi = await evalJs(`document.getElementById("usage-kpis").textContent`);
    check("KPI 含累计 Token 数", /累计\s*Token\s*数/.test(kpi), kpi.slice(0, 150));
    check("KPI 含峰值 Token 数", /峰值\s*Token\s*数/.test(kpi), kpi.slice(0, 150));
    check("KPI 含最长单次时长", kpi.includes("最长单次时长"), kpi.slice(0, 150));
    check("KPI 含当前连续天数=2 天（今天+昨天）", /当前连续天数/.test(kpi) && /2 天/.test(kpi), kpi.slice(0, 220));
    check("KPI 含最长连续天数", kpi.includes("最长连续天数"), kpi.slice(0, 150));

    // 3) Token 活动热力图：默认每日模式，40 天前的老数据在窗口内
    const heatRects = await evalJs(`document.querySelectorAll("#usage-heat svg rect").length`);
    check("热力图每日模式有格子", heatRects >= 40, "rects=" + heatRects);
    const heatMon = await evalJs(`document.querySelectorAll("#usage-heat .uh-mon").length`);
    check("热力图有月份标签", heatMon >= 1, "labels=" + heatMon);
    // 三粒度切换
    await evalJs(`setUsageHeatMode("week"); "ok"`);
    await sleep(300);
    const weekActive = await evalJs(`document.querySelector("#usage-heat-modes [data-heat=week]").classList.contains("active")`);
    const weekRows = await evalJs(`(() => {
      const svg = document.querySelector("#usage-heat svg");
      const ys = new Set([...svg.querySelectorAll("rect")].map(r => r.getAttribute("y")));
      return ys.size;
    })()`);
    check("每周模式切换单行格子", weekActive && weekRows === 1, "active=" + weekActive + " rows=" + weekRows);
    await evalJs(`setUsageHeatMode("total"); "ok"`);
    await sleep(300);
    const totalMon = await evalJs(`document.querySelectorAll("#usage-heat .uh-mon").length`);
    check("累计模式按月格子", totalMon >= 2, "labels=" + totalMon);
    await evalJs(`setUsageHeatMode("day"); "ok"`);
    await sleep(300);

    // 4) 多模型折线：归一后 6 组 → Top5 + 其他；无小写重复图例
    const trendPaths = await evalJs(`document.querySelectorAll("#usage-trend svg path").length`);
    check("折线有曲线（Top5+其他 = 6 条）", trendPaths >= 6, "paths=" + trendPaths);
    const legend = await evalJs(`document.querySelector("#usage-trend .um-legend")?.textContent || ""`);
    check("图例含归一后的 GLM-5.3-Flash", legend.includes("GLM-5.3-Flash"), legend);
    check("图例无小写重复行", !/(^|\s)glm-5\.3-flash/.test(legend), legend);
    check("图例含「其他」归组", legend.includes("其他"), legend);

    // 5) 模型用量甜甜圈：6 段 + 中心总量 + 列表行
    const segs = await evalJs(`document.querySelectorAll("#usage-models .ud-seg").length`);
    check("甜甜圈有分段", segs >= 6, "segs=" + segs);
    const center = await evalJs(`document.querySelector("#usage-models .ud-total")?.textContent`);
    check("甜甜圈中心有总量", /\d/.test(center || ""), center);
    const rows = await evalJs(`document.querySelectorAll("#usage-models .ud-row").length`);
    check("甜甜圈列表行数=分段数", rows === segs, "rows=" + rows + " segs=" + segs);
    const listTxt = await evalJs(`document.getElementById("usage-models").textContent`);
    check("列表含其他模型兜底", listTxt.includes("其他模型"), listTxt.slice(0, 160));
    const glmRow = await evalJs(`[...document.querySelectorAll("#usage-models .ud-name")]
      .map(n => n.textContent).find(s => s.includes("GLM"))`);
    check("甜甜圈列表 GLM 归一无小写重复", (glmRow || "").includes("GLM-5.3-Flash"), glmRow);

    // 6) 今天范围：单日仍走构成条（不是折线）
    await evalJs(`setUsageDays(1); "ok"`);
    await sleep(1000);
    const single = await evalJs(`!!document.querySelector("#usage-trend .usage-single")`);
    check("单日范围走构成条", single);

    // 7) 服务端新字段齐全
    const api = await fetch(SERVICE + "/api/usage?days=30").then((r) => r.json());
    check("API 含 by_day_model/all_by_day/新 totals",
      Array.isArray(api.by_day_model) && Array.isArray(api.all_by_day)
      && "peak_tokens" in api.totals && "streak_current" in api.totals
      && "streak_longest" in api.totals && "max_duration_s" in api.totals,
      JSON.stringify(Object.keys(api)).slice(0, 200));

    await shot("usage-stats-new.png");
    console.log("\n截图: .ui-shots/usage-stats-new.png");
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
