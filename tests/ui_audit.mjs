/* 全面 UI 体检：捕获控制台异常 + 逐页渲染断言 + 布局度量（不用肉眼看图）。
 * 场景：① 空台账（真实 data 无 usage）② 有数据。两轮都跑。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18795;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9338;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
              "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"].find(() => true);

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withBrowser(dataDir, label, fn) {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-audit-"));
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"],
    { cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT } });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) {}
    }
    check(`[${label}] 服务启动`, up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) {}
    }
    check(`[${label}] Edge CDP 就绪`, !!target);
    if (!target) throw new Error("no target");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map(); const errors = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.exceptionThrown") {
        errors.push("EXCEPTION: " + (m.params?.exceptionDetails?.exception?.description
          || m.params?.exceptionDetails?.text || "?"));
      }
      if (m.method === "Runtime.consoleAPICalled" && m.params?.type === "error") {
        errors.push("console.error: " + (m.params.args || []).map((a) => a.value ?? a.description).join(" "));
      }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) return "__ERR__ " + (r.result.exceptionDetails.exception?.description || "");
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await fn({ js, send, errors, tmp, label });
    await sleep(300);
    check(`[${label}] 无 JS 异常/错误日志`, errors.length === 0, errors.join(" ｜ "));
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    await sleep(800);
    for (const p of [edge, svc]) if (p?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) {}
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}

async function auditPages(js, label) {
  const PAGES = ["tasks", "runs", "usage", "agents", "models", "bindings", "orch", "appearance"];
  for (const p of PAGES) {
    await js(`switchTab("${p}"); "ok"`);
    await sleep(p === "usage" ? 1400 : 700);
    const visible = await js(`document.querySelectorAll("#page-settings .subpage:not(.hidden)").length`);
    check(`[${label}] ${p} 页恰好一个子页可见`, visible === 1, "可见数=" + visible);
    const title = await js(`document.getElementById("page-title").textContent`);
    check(`[${label}] ${p} 页标题非空`, !!title && title !== "设置", title);
  }
}

/* 图标可访问名称纪律（docs/ui-design.md §3）：纯图标交互件必须有
 * aria-label / title / data-i18n-title / aria-labelledby 之一；装饰图标
 * 应 aria-hidden。禁用态豁免（临时禁用不是终态）。 */
async function auditIconA11y(js, label) {
  const bad = await js(`(() => {
    const out = [];
    document.querySelectorAll("button, .icon-btn, [role=button]").forEach((el) => {
      if (el.disabled) return;
      const text = (el.innerText || "").trim();
      if (text || !el.querySelector("svg")) return;
      const named = el.getAttribute("aria-label") || el.getAttribute("title")
        || el.getAttribute("data-i18n-title") || el.getAttribute("aria-labelledby");
      if (!named) out.push(el.outerHTML.replace(/\\s+/g, " ").slice(0, 90));
    });
    return out;
  })()`);
  check(`[${label}] 纯图标按钮都有可访问名称`, !bad || bad.length === 0, (bad || []).join(" ｜ "));
}

async function main() {
  // ---------- 场景 A：空台账 ----------
  const emptyData = join(mkdtempSync(join(tmpdir(), "tutti-empty-")), "data");
  mkdirSync(emptyData, { recursive: true });
  await withBrowser(emptyData, "空台账", async ({ js, errors }) => {
    await auditPages(js, "空台账");
    await auditIconA11y(js, "空台账");
    await js(`switchTab("usage"); "ok"`);
    await sleep(1400);
    const kpi = await js(`document.getElementById("usage-kpis").textContent`);
    check("[空台账] KPI 不显示「加载中」", !/加载中/.test(kpi), kpi.slice(0, 80));
    check("[空台账] KPI 不显示「加载失败」", !/加载失败/.test(kpi), kpi.slice(0, 120));
    const trend = await js(`document.getElementById("usage-trend").innerHTML`);
    check("[空台账] 趋势区有内容（空态提示或图）", trend.length > 10, trend.slice(0, 100));
    const dims = await js(`document.getElementById("usage-dims").textContent`);
    check("[空台账] 维度区渲染 5 个标题", (dims.match(/按/g) || []).length >= 5, dims.slice(0, 150));
    const recent = await js(`document.getElementById("usage-recent").innerHTML`);
    check("[空台账] 最近调用有空态", recent.length > 5, recent.slice(0, 100));
    // 布局：KPI 卡是否溢出容器
    const overflow = await js(`(() => {
      const box = document.getElementById("usage-kpis");
      if (!box) return "no box";
      const bad = [...box.children].filter(c => c.scrollWidth > c.clientWidth + 2)
        .map(c => c.textContent.slice(0, 20));
      return bad.length ? bad.join(" | ") : "";
    })()`);
    check("[空台账] KPI 卡片文字无横向溢出", overflow === "", overflow);
    // 趋势图 SVG 是否被撑破（空台账默认「今天」时无数据 → 渲染空态文案，跳过几何断言）
    const svgFit = await js(`(() => {
      const svg = document.querySelector("#usage-trend svg");
      if (!svg) return document.getElementById("usage-trend").textContent.trim()
        ? "" : "趋势区既无图也无空态提示";
      const r = svg.getBoundingClientRect();
      const p = svg.parentElement.getBoundingClientRect();
      return (r.width > p.width + 2) ? ("svg " + Math.round(r.width) + " > parent " + Math.round(p.width)) : "";
    })()`);
    check("[空台账] 趋势区为图或无溢出空态", svgFit === "", svgFit);
  });

  // ---------- 场景 B：有数据 ----------
  const dataDir = join(mkdtempSync(join(tmpdir(), "tutti-seed-")), "data");
  mkdirSync(join(dataDir, "usage"), { recursive: true });
  const day = (o) => new Date(Date.now() + o * 864e5).toISOString().slice(0, 10);
  const ym = day(0).slice(0, 7).replace("-", "");
  const rows = [
    { day: day(0), tool: "codex", model: "gpt-5", role: "implement", total: 120000, input: 90000, output: 30000 },
    { day: day(0), tool: "orchestrator", agent: "orchestrator", agent_label: "编排者", model: "orch-x", role: "plan", total: 8000, input: 6000, output: 2000 },
    { day: day(-1), tool: "claude", agent: "claude-code", model: "claude-sonnet-4-5-20250929", role: "review", total: 45000, input: 30000, output: 15000 },
    { day: day(-3), tool: "codex", model: "gpt-5", role: "fix-2", ok: false, total: 12000, input: 10000, output: 2000 },
  ].map((r) => JSON.stringify({
    ts: r.day + " 10:00:00", day: r.day, source: "pipeline", run_id: "r1", task_id: "t1",
    task_type: "code", role: r.role, agent: r.agent || "codex-cli",
    agent_label: r.agent_label || "Codex CLI", tool: r.tool, model: r.model, provider: "",
    ok: r.ok !== false, duration_s: 30, cost_usd: 0.05,
    input: r.input, output: r.output, cached: 0, reasoning: 0, total: r.total,
  })).join("\n") + "\n";
  writeFileSync(join(dataDir, "usage", "usage-" + ym + ".jsonl"), rows, "utf8");

  await withBrowser(dataDir, "有数据", async ({ js, send }) => {
    await auditPages(js, "有数据");
    await auditIconA11y(js, "有数据");
    // 默认范围是「今天」，种子数据跨多天，先显式切到 30 天再断言总量
    await js(`S.usageDays = 30; switchTab("usage"); "ok"`);
    await sleep(1400);
    const kpi = await js(`document.getElementById("usage-kpis").textContent`);
    check("[有数据] 30 天总 tokens 显示 18.5万", /18\.5万|185,?000/.test(kpi), kpi.slice(0, 100));
    check("[有数据] 失败数 1", /失败\s*1/.test(kpi), kpi.slice(0, 160));
    // 维度表：长模型名是否被裁
    const clip = await js(`(() => {
      const bad = [...document.querySelectorAll("#usage-dims .bar-outer span")]
        .filter(s => s.scrollWidth > s.clientWidth + 1).map(s => s.textContent);
      return bad.length ? bad.join(" | ") : "";
    })()`);
    check("[有数据] 维度表名称未被裁切", clip === "", clip);
    // 最近调用：模型列是否溢出单元格
    const rclip = await js(`(() => {
      const bad = [...document.querySelectorAll("#usage-recent td")]
        .filter(t => t.scrollWidth > t.clientWidth + 2).map(t => t.textContent.slice(0, 24));
      return bad.length ? bad.join(" | ") : "";
    })()`);
    check("[有数据] 最近调用表格无溢出", rclip === "", rclip);
    // 时间范围按钮：点「全部」后 active 类正确
    await js(`setUsageDays(0); "ok"`);
    await sleep(900);
    const act = await js(`[...document.querySelectorAll("#usage-ranges [data-days]")]
      .filter(b => b.classList.contains("active")).map(b => b.dataset.days).join(",")`);
    check("[有数据] 全部按钮唯一 active", act === "0", "active=" + act);
    const allKpi = await js(`document.getElementById("usage-kpis").textContent`);
    check("[有数据] 全部=18.5万（同 30 天窗口，种子均在 40 天内）", /18\.5万|185,?000/.test(allKpi), allKpi.slice(0, 90));
    // 折线/柱形数量
    const rects = await js(`document.querySelectorAll("#usage-trend svg rect").length`);
    check("[有数据] 趋势图有柱形", rects >= 3, "rects=" + rects);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", "audit-usage.png"), Buffer.from(r.result.data, "base64"));
    });
    // 移动端视口
    await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await sleep(800);
    const mobOverflow = await js(`(() => {
      const de = document.documentElement;
      return de.scrollWidth > de.clientWidth + 2
        ? ("横向滚动 " + de.scrollWidth + " > " + de.clientWidth) : "";
    })()`);
    check("[有数据] 手机视口无横向滚动", mobOverflow === "", mobOverflow);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "audit-usage-mobile.png"), Buffer.from(r.result.data, "base64"));
    });
  });

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== UI 体检：%d 通过 / %d 失败 =====", results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项："); bad.forEach((b) => console.log("  - " + b.name)); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
