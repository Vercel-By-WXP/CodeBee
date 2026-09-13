/* 对【已在运行的真实服务】做用量页验收：不启服务、不写数据，只开浏览器读取。
 * 用法：node tests/ui_probe_live.mjs [port]   （默认 8765） */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.argv[2] || 8765);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9343;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
};

async function main() {
  let up = false;
  try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* not up */ }
  check(`真实服务在 ${PORT} 运行`, up);
  if (!up) { console.log("跳过：请先启动 Tutti"); process.exit(1); }

  const tmp = mkdtempSync(join(tmpdir(), "tutti-live-"));
  let edge = null, ws = null;
  try {
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
    check("Edge CDP 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const errs = [];
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
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await js(`switchTab("usage"); "ok"`);
    await sleep(2500);

    const dump = await js(`(() => {
      const out = [];
      out.push("### KPI");
      [...document.querySelectorAll("#usage-kpis .kpi")].forEach(k => out.push("  · " +
        k.querySelector(".kpi-v").textContent + "  |  " + k.querySelector(".kpi-l").textContent +
        "  |  " + (k.querySelector(".kpi-s")?.textContent || "")));
      out.push("### 趋势  柱形=" + document.querySelectorAll("#usage-trend svg rect").length +
        "  日期=" + [...document.querySelectorAll("#usage-trend .uc-x")].map(t=>t.textContent).join(","));
      out.push("### 维度表");
      [...document.querySelectorAll("#usage-dims .sec-title")].forEach(h => {
        const t = h.nextElementSibling;
        out.push("  ▸ " + h.textContent + (t && t.tagName === "TABLE" ? "（" + t.querySelectorAll("tbody tr").length + " 行）" : "（无表格）"));
        if (t && t.tagName === "TABLE") [...t.querySelectorAll("tbody tr")].slice(0,4).forEach(tr =>
          out.push("      " + [...tr.children].map(c=>c.textContent.trim()).join(" | ")));
      });
      out.push("### 最近调用");
      const rt = document.querySelector("#usage-recent table");
      if (rt) [...rt.querySelectorAll("tbody tr")].slice(0,4).forEach(tr =>
        out.push("  " + [...tr.children].map(c=>c.textContent.trim()).join(" | ")));
      else out.push("  " + document.getElementById("usage-recent").textContent.slice(0,60));
      return out.join("\\n");
    })()`);
    console.log("\n---------- 真实服务用量页 dump ----------");
    console.log(dump);
    console.log("----------------------------------------\n");

    check("KPI 无「加载中」残留", !/加载中/.test(await js(`document.getElementById("usage-kpis").textContent`)));
    check("KPI 无「加载失败」", !/加载失败/.test(await js(`document.getElementById("usage-kpis").textContent`)));
    check("趋势区有 SVG", (await js(`document.querySelectorAll("#usage-trend svg").length`)) === 1);
    check("五个维度表都渲染", (await js(`document.querySelectorAll("#usage-dims .sec-title").length`)) === 5);
    check("最近调用有行", (await js(`document.querySelectorAll("#usage-recent tbody tr").length`)) > 0);
    // 横向溢出
    const of = await js(`(() => {
      const bad = [...document.querySelectorAll("#usage-dims .bar-outer span, #usage-recent td")]
        .filter(e => e.scrollWidth > e.clientWidth + 2).map(e => e.textContent.slice(0,20));
      return bad.length ? bad.join(" | ") : "";
    })()`);
    check("表格无横向溢出", of === "", of);
    check("全程无 JS 异常", errs.length === 0, errs.join(" ｜ "));
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", "live-usage.png"), Buffer.from(r.result.data, "base64"));
    });
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 真实服务验收：%d 通过 / %d 失败 =====", results.length - bad.length, bad.length);
  if (bad.length) { bad.forEach((b) => console.log("  ✗ " + b.name)); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
