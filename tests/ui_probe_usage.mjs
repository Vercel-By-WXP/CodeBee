/* 用量页深度体检：dump 渲染后的文本结构（代替肉眼看图）+ poll 周期稳定性。
 * 用法：node tests/ui_probe_usage.mjs   （自起临时服务，预置种子数据） */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18794;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9340;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

function seed(dataDir) {
  mkdirSync(join(dataDir, "usage"), { recursive: true });
  const day = (o) => new Date(Date.now() + o * 864e5).toISOString().slice(0, 10);
  const ym = day(0).slice(0, 7).replace("-", "");
  const mk = (o) => JSON.stringify({
    ts: o.day + " 09:00:00", day: o.day, source: "pipeline", run_id: "r1", task_id: "t1",
    task_type: o.tt || "code", role: o.role, agent: o.agent || "codex-cli",
    agent_label: o.label || "Codex CLI", tool: o.tool, model: o.model, provider: "",
    ok: o.ok !== false, duration_s: o.dur || 30, cost_usd: o.cost || 0,
    input: o.input, output: o.output, cached: o.cached || 0, reasoning: 0,
    total: o.input + o.output + (o.cached || 0),
  });
  const rows = [
    mk({ day: day(0), tool: "codex", model: "gpt-5.2-codex", role: "implement", input: 90000, output: 30000, cost: 0.42, dur: 210 }),
    mk({ day: day(0), tool: "orchestrator", agent: "orchestrator", label: "编排者", model: "claude-opus-4", role: "plan", input: 6000, output: 2000, cost: 0.05 }),
    mk({ day: day(-1), tool: "claude", agent: "claude-code", label: "[CC]", model: "claude-sonnet-4-5", role: "review", input: 30000, output: 15000, cached: 40000, cost: 0.31, dur: 88 }),
    mk({ day: day(-2), tool: "codex", model: "gpt-5.2-codex", role: "fix-2", ok: false, input: 10000, output: 2000, cost: 0.02, tt: "novel" }),
    mk({ day: day(-9), tool: "claude", agent: "claude-code", label: "[CC]", model: "claude-sonnet-4-5", role: "draft-c1", input: 22000, output: 9000, cached: 5000, cost: 0.18, tt: "novel" }),
  ].join("\n") + "\n";
  writeFileSync(join(dataDir, "usage", "usage-" + ym + ".jsonl"), rows, "utf8");
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-probe-"));
  const dataDir = join(tmp, "data");
  seed(dataDir);
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"],
    { cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT } });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("服务启动", up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge CDP 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map(); const errs = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.exceptionThrown") {
        errs.push(m.params?.exceptionDetails?.exception?.description || m.params?.exceptionDetails?.text);
      }
      if (m.method === "Runtime.consoleAPICalled" && m.params?.type === "error") {
        errs.push((m.params.args || []).map((a) => a.value ?? a.description).join(" "));
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
    await send("Runtime.enable"); await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await js(`switchTab("usage"); "ok"`);
    await sleep(1500);

    // ---- 文本化 dump：代替看图 ----
    const dump = await js(`(() => {
      const out = [];
      out.push("### KPI");
      [...document.querySelectorAll("#usage-kpis .kpi")].forEach(k => {
        out.push("  · " + k.querySelector(".kpi-v").textContent + "  |  " +
          k.querySelector(".kpi-l").textContent + "  |  " +
          (k.querySelector(".kpi-s")?.textContent || ""));
      });
      out.push("### 趋势");
      const svg = document.querySelector("#usage-trend svg");
      out.push("  svg=" + (svg ? "有" : "无") + " 柱形=" + document.querySelectorAll("#usage-trend svg rect").length +
        " 日期标签=" + [...document.querySelectorAll("#usage-trend .uc-x")].map(t=>t.textContent).join(","));
      out.push("### 维度表");
      [...document.querySelectorAll("#usage-dims .sec-title")].forEach(h => {
        const tbl = h.nextElementSibling;
        out.push("  ▸ " + h.textContent + (tbl && tbl.tagName === "TABLE"
          ? "（" + (tbl.querySelectorAll("tbody tr").length) + " 行）" : "（无表格：" + (tbl?tbl.tagName:"null") + "）"));
        if (tbl && tbl.tagName === "TABLE") {
          [...tbl.querySelectorAll("tbody tr")].forEach(tr => {
            const td = [...tr.children].map(c => c.textContent.trim());
            out.push("      " + td.join(" | "));
          });
        }
      });
      out.push("### 最近调用");
      const rt = document.querySelector("#usage-recent table");
      if (rt) [...rt.querySelectorAll("tbody tr")].forEach(tr => {
        out.push("  " + [...tr.children].map(c=>c.textContent.trim()).join(" | "));
      });
      else out.push("  （无表格）" + document.getElementById("usage-recent").textContent.slice(0,60));
      return out.join("\\n");
    })()`);
    console.log("\n---------- 用量页渲染 dump（30 天）----------");
    console.log(dump);
    console.log("-------------------------------------------\n");

    // ---- poll 周期稳定性：等过一轮 8s 轮询 + SSE ----
    const before = await js(`document.getElementById("usage-kpis").textContent`);
    await sleep(10000);
    const after = await js(`document.getElementById("usage-kpis").textContent`);
    check("停留 10s（跨 poll 周期）KPI 内容不变", before === after && before.length > 10,
      "before=" + String(before).slice(0, 60) + " after=" + String(after).slice(0, 60));
    const stillVisible = await js(`!document.getElementById("sub-usage").classList.contains("hidden")`);
    check("poll 后用量页仍可见", stillVisible === true);

    // ---- 切走再切回，数据仍在 ----
    await js(`switchTab("tasks"); "ok"`);
    await sleep(500);
    await js(`switchTab("usage"); "ok"`);
    await sleep(1200);
    const back = await js(`document.getElementById("usage-kpis").textContent`);
    check("切走再切回数据仍在", back.length > 10 && !/加载失败/.test(back), back.slice(0, 80));

    check("全程无 JS 异常", errs.length === 0, errs.join(" ｜ "));
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", "probe-usage.png"), Buffer.from(r.result.data, "base64"));
    });
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) if (p?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 用量页深度体检：%d 通过 / %d 失败 =====", results.length - bad.length, bad.length);
  if (bad.length) { bad.forEach((b) => console.log("  ✗ " + b.name)); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
