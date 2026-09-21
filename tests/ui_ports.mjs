/* 端口占用诊断 UI 验证（借鉴 leftopen）：Edge headless + CDP（零依赖，沿 ui_check 模板）。
 * 自起临时服务（TUTTI_DATA=临时目录、随机高位端口）→ 断言 /api/ports 能看到服务
 * 自身端口且 self=true → 设置页扫描按钮渲染表格（本服务行无关闭钮）→ 自身端口
 * 温和关闭被拒（409「不能关闭自身服务进程」）→ 越界端口 400 → 清理进程。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// 随机高位端口 + 随机 CDP 口：多代理并行不互撞（18798/固定口曾双绑互驱假红）
const PORT = 18700 + (process.pid % 250);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9400 + (process.pid % 400);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-ports-"));

  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪（TUTTI_DATA 隔离）", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-ports-"));
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
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);

    const ws = new WebSocket(target.webSocketDebuggerUrl);
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
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      return r.result?.result?.value;
    };

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    // 1) API 直读：能看到服务自身端口且 self=true（页面上下文带 authHeaders）
    const api1 = JSON.parse(await evalJs(`
      (async () => JSON.stringify(await (await fetch("/api/ports", { headers: authHeaders() })).json()))()`));
    const mine = (api1.ports || []).find((p) => p.port === PORT);
    check("/api/ports 返回列表且含服务自身端口", Array.isArray(api1.ports) && !!mine, JSON.stringify(api1).slice(0, 160));
    check("自身端口条目 self=true 且有 PID/进程名", !!mine && mine.self === true && !!mine.pid && !!mine.process,
      JSON.stringify(mine));

    // 2) ?port= 单端口过滤
    const api2 = JSON.parse(await evalJs(`
      (async () => JSON.stringify(await (await fetch("/api/ports?port=${PORT}", { headers: authHeaders() })).json()))()`));
    check("?port=N 过滤只剩单条", (api2.ports || []).length === 1 && api2.ports[0].port === PORT,
      JSON.stringify(api2).slice(0, 160));

    // 3) 设置页「端口占用」区：扫描按钮 → 表格渲染（PowerShell 补进程名可能要几秒）
    await evalJs(`switchTab("about"); "ok"`);
    await sleep(800);
    await evalJs(`scanPorts(); "ok"`);
    let table = null;
    for (let i = 0; i < 24 && !table; i++) {
      await sleep(1000);
      const raw = await evalJs(`JSON.stringify({
        rows: document.querySelectorAll("#ports-list tbody tr").length,
        ownRow: Array.from(document.querySelectorAll("#ports-list tbody tr"))
          .some((tr) => tr.textContent.includes("${PORT}")),
        ownNoClose: (() => { const tr = Array.from(document.querySelectorAll("#ports-list tbody tr"))
          .find((tr) => tr.textContent.includes("${PORT}"));
          return tr ? !tr.querySelector("button") : false; })(),
        hasCloseBtn: !!document.querySelector("#ports-list tbody tr button"),
        failed: /扫描失败/.test(document.getElementById("ports-list").textContent || "")
      })`);
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed && !parsed.failed && parsed.rows > 0) { table = parsed; break; }
    }
    check("扫描后表格渲染出多行", !!table && table.rows > 0, JSON.stringify(table));
    check("表格含服务自身端口所在行", !!table && table.ownRow, table && JSON.stringify(table));
    check("本服务行没有关闭按钮（自身拒关）", !!table && table.ownNoClose, table && JSON.stringify(table));
    check("其他进程行有关闭按钮", !!table && table.hasCloseBtn, table && JSON.stringify(table));

    // 4) 自身端口温和关闭 → 409 拒绝，toast 报「不能关闭自身服务进程」
    //    （close_port 内部两次端口扫描，机器忙时可能超过 1.5s，轮询等待）
    await evalJs(`window.confirm = () => true; closePort(${PORT}); "pending"`);
    let toast1 = "";
    for (let i = 0; i < 24 && !toast1; i++) {
      await sleep(1000);
      toast1 = await evalJs(`document.getElementById("toast")?.textContent || ""`);
      if (!/不能关闭自身服务进程/.test(toast1 || "")) toast1 = "";
    }
    check("自身端口关闭被拒并 toast 人话", /不能关闭自身服务进程/.test(toast1 || ""), toast1);

    // 5) 越界端口 → 400
    const api3 = JSON.parse(await evalJs(`
      (async () => { const r = await fetch("/api/ports/close", { method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ port: 99999 }) });
        return JSON.stringify({ status: r.status, body: await r.json() }); })()`));
    check("越界端口 400 拒绝", api3.status === 400, JSON.stringify(api3).slice(0, 160));

    // 6) 端口占用「项目归属」字段在响应结构里（本机 Temp 目录下进程归属应为空或项目名，字段必须在）
    check("端口条目含 project/local_only 字段",
      !!mine && "project" in mine && "local_only" in mine, JSON.stringify(mine));
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }

  const fails = results.filter((r) => !r.ok);
  console.log(fails.length ? `\n${fails.length}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
