/* 新建任务「默认保存路径」核验（Edge headless + CDP，临时端口 18843）：
 * 工作目录不再用 localStorage「上次用过目录」预填——旧记忆会盖过设置里的默认路径。
 * 1) 首屏 f-workdir 留空，hint 显示「留空则保存到：<默认路径>」；
 * 2) 旧版残留键 orch.workdir 启动即清理；
 * 3) 手工塞入旧记忆再刷新：字段仍留空、hint 仍指默认路径（旧记忆被忽略）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18843;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9365;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-wddef-"));
  const dataDir = join(tmp, "data");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });

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
    const settings = await (await fetch(SERVICE + "/api/settings")).json();
    const effective = settings.default_workdir_effective || "";
    check("服务端有生效的默认保存路径", !!effective, JSON.stringify(settings).slice(0, 200));

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

    const formState = `JSON.stringify((() => ({
      wd: document.getElementById("f-workdir").value,
      hint: document.getElementById("f-workdir-hint").textContent,
      stale: localStorage.getItem("orch.workdir") }))())`;
    let st = JSON.parse(await evalJs(formState));
    check("首屏：工作目录字段留空（不预填旧记忆）", st.wd === "", JSON.stringify(st));
    check("首屏：旧版 orch.workdir 残留键已清理", st.stale === null || st.stale === "", JSON.stringify(st));
    check("首屏：hint 指向默认保存路径",
      st.hint.includes("留空则保存到") && st.hint.includes(effective), JSON.stringify(st));

    /* 旧记忆场景：塞入上次用过的目录 → 刷新后必须被忽略，字段仍留空走默认 */
    await evalJs(`localStorage.setItem("orch.workdir", "E:\\\\stale-old-dir"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    st = JSON.parse(await evalJs(formState));
    check("刷新后：旧记忆不预填，字段仍留空", st.wd === "", JSON.stringify(st));
    check("刷新后：旧记忆键被清理", st.stale === null || st.stale === "", JSON.stringify(st));
    check("刷新后：hint 仍指向默认保存路径",
      st.hint.includes("留空则保存到") && st.hint.includes(effective), JSON.stringify(st));
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
