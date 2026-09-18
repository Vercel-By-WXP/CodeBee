/* 帮助改进（遥测开关 + 诊断包 + 一键反馈 Issue）端到端核验（Edge headless + CDP，临时端口 18861）：
 * 1) 服务端设置默认 telemetry_errors=true（错误类默认开，可一键关）；
 * 2) 「关于与更新」面板 #set-telemetry 存在且勾选态跟服务端一致；
 * 3) UI 里关掉开关 → POST /api/settings 落盘 → 刷新后仍为关；
 * 4) /api/diagnostics/bundle 返回 zip（PK 魔数），页面内带鉴权 fetch 可取；
 * 5) #diag-export 按钮真实点击 → 成功 toast（导出链路 fetch→blob→toast 全通）；
 * 6) 种一条错误记录 → /api/diagnostics/issue-summary 聚合出 TIMEOUT 且假密钥被剥；
 * 7) #issue-report 点击 → window.open 打开 GitHub Issue 新建页且 body 含摘要。
 * （假密钥为运行时拼接的假样本，仅验证脱敏，非真实凭据。） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const FAKE_KEY = "sk-" + "abcd".repeat(4) + "1234";

const PORT = 18861;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9381;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-telemetry-"));
  const dataDir = join(tmp, "data");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });

  // 种一条错误记录（服务启动前落盘；detail 里故意埋假密钥，验证摘要二次脱敏）
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:00:00`;
  const seeded = { id: ts + "-seed0001", ts, day: ts.slice(0, 10), category: "step",
    reason: "TIMEOUT", detail: "key " + FAKE_KEY + " boom", provider: "p1", model: "m1",
    tool: "codex", role: "draft", run_id: "r1", task_id: "t1", step: 1,
    exit_code: null, app_version: "0.0.0", os: "windows" };
  mkdirSync(join(dataDir, "errors"), { recursive: true });
  writeFileSync(join(dataDir, "errors", "errors-" + ts.slice(0, 7).replace("-", "") + ".jsonl"),
    JSON.stringify(seeded) + "\n");

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

    const st0 = await (await fetch(SERVICE + "/api/settings")).json();
    check("服务端默认 telemetry_errors=true", st0.telemetry_errors === true,
      JSON.stringify(st0).slice(0, 200));

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

    let ui = JSON.parse(await evalJs(`JSON.stringify((() => ({
      exists: !!document.getElementById("set-telemetry"),
      checked: (document.getElementById("set-telemetry") || {}).checked === undefined ? null
               : document.getElementById("set-telemetry").checked
    }))())`));
    check("关于页遥测开关存在", ui.exists, JSON.stringify(ui));
    check("开关勾选态与服务端一致（默认开）", ui.checked === true, JSON.stringify(ui));

    // UI 关闭开关（触发 onchange → saveTelemetry → POST 落盘）
    await evalJs(`(() => { const t = document.getElementById("set-telemetry");
      t.checked = false; t.dispatchEvent(new Event("change")); return "ok"; })()`);
    await sleep(1200);
    const st1 = await (await fetch(SERVICE + "/api/settings")).json();
    check("UI 关闭后服务端 telemetry_errors=false", st1.telemetry_errors === false,
      JSON.stringify(st1).slice(0, 120));

    // 刷新持久化
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    ui = JSON.parse(await evalJs(`JSON.stringify({ checked: document.getElementById("set-telemetry").checked })`));
    check("刷新后开关仍为关（已持久化）", ui.checked === false, JSON.stringify(ui));

    // 诊断包端点（页面内带鉴权头）：zip 魔数 + 类型
    const bundle = JSON.parse(await evalJs(`(async () => {
      const r = await fetch("/api/diagnostics/bundle", { headers: authHeaders() });
      const buf = new Uint8Array(await r.arrayBuffer());
      return JSON.stringify({ status: r.status, ctype: r.headers.get("Content-Type") || "",
        magic: String.fromCharCode(buf[0]) + String.fromCharCode(buf[1]), size: buf.length });
    })()`));
    check("诊断包 HTTP 200", bundle.status === 200, JSON.stringify(bundle));
    check("诊断包 Content-Type 是 zip", String(bundle.ctype).includes("application/zip"), JSON.stringify(bundle));
    check("诊断包是合法 zip（PK 魔数）", bundle.magic === "PK", JSON.stringify(bundle));
    check("诊断包非空", bundle.size > 200, JSON.stringify(bundle));

    // 导出按钮真实点击 → 成功 toast（整链路）
    const clicked = await evalJs(`(async () => {
      const b = document.getElementById("diag-export");
      if (!b) return "missing";
      b.click();
      for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 300));
        const t = document.getElementById("toast");
        if (t && t.classList.contains("show") && t.textContent) return t.textContent;
      }
      return "no-toast";
    })()`);
    check("点导出按钮出成功 toast", String(clicked).includes("诊断包已开始下载") ||
      String(clicked).includes("已开始下载"), String(clicked));

    // Issue 摘要端点：聚合出 TIMEOUT，假密钥被二次脱敏剥掉
    const summary = await (await fetch(SERVICE + "/api/diagnostics/issue-summary")).json();
    check("Issue 摘要返回 title/body", !!(summary.title && summary.body), JSON.stringify(summary).slice(0, 200));
    check("Issue 摘要聚合出 TIMEOUT", String(summary.title).includes("TIMEOUT"), summary.title);
    check("Issue 摘要不外泄假密钥（二次脱敏兜底）",
      !String(summary.body).includes(FAKE_KEY) && String(summary.body).includes("[key]"), "");
    check("Issue 摘要明细带供应商/模型", String(summary.body).includes("p1/m1"), "");

    // 一键反馈按钮：stub window.open 捕获 URL（防止测试期真开页）
    const issueUrl = await evalJs(`(async () => {
      const b = document.getElementById("issue-report");
      if (!b) return "missing";
      window.open = (u) => { window.__issueUrl = u; return null; };
      b.click();
      for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 300));
        if (window.__issueUrl) return window.__issueUrl;
      }
      return "no-open";
    })()`);
    check("点反馈按钮打开 GitHub Issue 新建页",
      String(issueUrl).startsWith("https://github.com/Vercel-By-WXP/CodeBee/issues/new?"),
      String(issueUrl).slice(0, 120));
    let decoded = "";
    try {
      const q = String(issueUrl).split("?")[1] || "";
      decoded = decodeURIComponent((new URLSearchParams(q)).get("body") || "");
    } catch (e) { /* 解码失败按空处理 */ }
    check("Issue 预填正文含 TIMEOUT 聚合", decoded.includes("TIMEOUT"), decoded.slice(0, 200));
    check("预填正文同样不外泄假密钥", !decoded.includes(FAKE_KEY), "");

    // 恢复开启（清理语义：本用例独立数据目录，随手还原默认态验证正向路径）
    await evalJs(`(() => { const t = document.getElementById("set-telemetry");
      t.checked = true; t.dispatchEvent(new Event("change")); return "ok"; })()`);
    await sleep(1200);
    const st2 = await (await fetch(SERVICE + "/api/settings")).json();
    check("重新开启落盘 telemetry_errors=true", st2.telemetry_errors === true, JSON.stringify(st2).slice(0, 120));
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
