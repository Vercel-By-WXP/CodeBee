/* 编排设置页对齐核验：量实际几何（Edge headless + CDP，端口 18814）。
 * 断言：ops 行 flex 布局且开关/按钮垂直居中对齐；orch-config 与上方 hint 有间距；
 * 运行设置两个 .field 之间不再贴合；settings-msg 与上方有间距。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18814;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9344;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-align-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {}
    }
    check("临时服务启动", up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) {}
    }
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

    const g = await evalJs(`(async () => {
      switchTab("orch");
      await new Promise(r => setTimeout(r, 1200));
      const r = (el) => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
      const panel = document.querySelector("#sub-orch .panel");
      const hint = panel.querySelector("p.hint");
      const cfg = document.getElementById("orch-config");
      const ops = cfg.querySelector(".ops");
      const toggle = ops.querySelector(".toggle");
      const btnSave = ops.querySelector("button");
      const grid2 = cfg.querySelector(".grid-2");
      const fields = [...panel.children].filter(el => el.classList.contains("field"));
      const msg = document.getElementById("settings-msg");
      const gap = (a, b) => +(b.y - (a.y + a.h)).toFixed(1);
      return JSON.stringify({
        opsDisplay: ops && getComputedStyle(ops).display,
        opsMidDiff: ops && toggle && btnSave ? Math.abs((r(toggle).y + r(toggle).h / 2) - (r(btnSave).y + r(btnSave).h / 2)) : null,
        cfgGap: hint && cfg ? gap(r(hint), r(cfg)) : null,
        opsGap: grid2 && ops ? gap(r(grid2), r(ops)) : null,
        fieldGap: fields.length === 2 ? gap(r(fields[0]), r(fields[1])) : null,
        msgGap: fields.length === 2 && msg ? gap(r(fields[1]), r(msg)) : null,
        nFields: fields.length,
      });
    })()`);
    const m = JSON.parse(g);
    check("ops 行为 flex 布局", m.opsDisplay === "flex", g);
    check("开关与按钮垂直居中对齐（中线差 < 2px）", m.opsMidDiff !== null && m.opsMidDiff < 2, g);
    check("orch-config 与顶部说明有间距（> 8px）", m.cfgGap !== null && m.cfgGap > 8, g);
    check("grid-2 与操作行有间距（> 8px）", m.opsGap !== null && m.opsGap > 8, g);
    check("运行设置两个 .field 之间有间距（> 8px）", m.nFields === 2 && m.fieldGap > 8, g);
    check("settings-msg 与上方有间距（> 4px）", m.msgGap !== null && m.msgGap > 4, g);

    const fails = results.filter((r) => !r.ok);
    console.log(fails.length ? "\n✗ " + fails.length + " 项未过" : "\n全部通过");
    process.exitCode = fails.length ? 1 : 0;
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(2); });
