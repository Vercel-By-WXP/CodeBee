/* 智能体目录「一键打开」按钮核验：Edge headless + CDP（临时服务端口 18815）。
 * 1) 已安装条目的卡片有「打开」/「打开网页」按钮（web 类标"打开网页"）；
 * 2) 未安装条目不出打开按钮；
 * 3) 按钮的 onclick 指向 openAgent('<id>')，与 /api/catalog 的 launch.kind 对齐；
 * 4) 打开按钮排在该卡片操作行首位（一键打开是主操作）。
 * 注意：只验渲染，不真点——点了会在用户机器上拉起真实 TUI/浏览器。
 * 请求全部发往硬编码的本机环回地址 127.0.0.1。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18815;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9345;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-launch-ui-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      // TUTTI_ISOLATE_HOME：launch/绑定同步会直写 ~/.claude 等真实 CLI 配置
      //（2026-09-22 a.test 毒配置案），UI 测试服务主目录必须隔离
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT, TUTTI_ISOLATE_HOME: "1" },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);

    // 后端事实：全部条目都该透出 launch，安装状态以检测为准
    const cat = (await (await fetch(SERVICE + "/api/catalog")).json()).catalog;
    check("catalog 全部条目透出 launch", cat.every((c) => c.launch && c.launch.command),
      JSON.stringify(cat.filter((c) => !c.launch).map((c) => c.id)));
    const installed = cat.filter((c) => c.installed);
    check("本机至少有一个已安装 CLI（dsh 或 codex 等）", installed.length >= 1, String(installed.map((c) => c.id)));

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
    await send("Runtime.evaluate", { expression: `window.alert=()=>true;window.confirm=()=>true;` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 进入智能体目录页，抓全部卡片上的打开按钮 */
    const dump = await evalJs(`(async () => {
      switchTab("agents");
      await new Promise(r => setTimeout(r, 1500));
      const cards = [...document.querySelectorAll("#ag-installed .card, #ag-installable .card")];
      const btns = cards.map((card) => {
        const name = card.querySelector(".name")?.textContent || "";
        const open = [...card.querySelectorAll(".ops button")].find((b) => /^打开(网页)?$/.test(b.textContent.trim()));
        const opsButtons = [...card.querySelectorAll(".ops button")];
        return {
          name,
          label: open ? open.textContent.trim() : "",
          onclick: open ? open.getAttribute("onclick") : "",
          firstInOps: open ? opsButtons[0] === open : false,
          primary: open ? open.classList.contains("primary") : false,
        };
      });
      return JSON.stringify({
        openAgentFn: typeof window.openAgent,
        btns: btns.filter((b) => b.label),
      });
    })()`);
    const d = JSON.parse(dump);
    check("openAgent 函数已定义", d.openAgentFn === "function", dump);

    // 与后端事实对齐：launch kind=web 的已安装条目 → 打开网页；console → 打开
    const byName = Object.fromEntries(cat.map((c) => [c.name, c]));
    const webNames = cat.filter((c) => c.launch?.kind === "web").map((c) => c.name);
    let okAll = d.btns.length === installed.length;
    let detail = "";
    for (const b of d.btns) {
      const entry = byName[b.name];
      const want = entry?.launch?.kind === "web" ? "打开网页" : "打开";
      const idOk = b.onclick === `openAgent('${entry?.id}')`;
      if (b.label !== want || !idOk || !b.firstInOps || !b.primary) {
        okAll = false;
        detail += JSON.stringify(b);
      }
    }
    check("已安装条目都有打开按钮且文案/onclick/首位对齐 launch", okAll,
      detail + "（按钮 " + d.btns.length + " / 已安装 " + installed.length + "）");
    check("web 类标「打开网页」（" + webNames.join("、") + "）",
      webNames.every((n) => d.btns.some((b) => b.name === n && b.label === "打开网页")), dump);
    const uninstalledNames = cat.filter((c) => !c.installed).map((c) => c.name);
    check("未安装条目不出打开按钮",
      uninstalledNames.every((n) => !d.btns.some((b) => b.name === n)), dump);

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
