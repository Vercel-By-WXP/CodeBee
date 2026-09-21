/* README 功能截图：Edge headless + CDP 驱动页面切页，逐页整屏截图到 docs/screenshots/。
 * 每页先 dump 标题/关键文本（脚本看不了图，靠文本确认页面正确）。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = "http://127.0.0.1:19921";
const CDP_PORT = 9347;
const OUT = new URL("../docs/screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUNS = process.argv[2] || "";   // 逗号分隔的 run id（A=连载运行中,B=代码已完成）

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "cb-shots-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1600,1000", "about:blank",
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
    if (!target) throw new Error("Edge CDP 未就绪");
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      pending.set(id, (m) => m.error ? rej(new Error(method + ": " + m.error.message)) : res(m.result));
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expression) => {
      const r = await send("Runtime.evaluate",
        { expression, returnByValue: true, awaitPromise: true, userGesture: true });
      if (r && r.exceptionDetails) console.log("EVAL-ERR:", r.exceptionDetails.text,
        String((r.exceptionDetails.exception || {}).description || "").slice(0, 160));
      return r && r.result ? r.result.value : undefined;
    };
    const shot = async (name) => {
      const s = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      if (!s || !s.data) throw new Error("captureScreenshot 无数据: " + JSON.stringify(s).slice(0, 200));
      const f = join(OUT, name + ".png");
      writeFileSync(f, Buffer.from(s.data, "base64"));
      console.log("saved", f);
    };
    const dump = async (tag) => {
      const info = await evalJs(`(document.title + " :: " + document.body.innerText).slice(0, 300).split("\\n").join(" | ")`);
      console.log("[" + tag + "]", info);
    };
    const nav = async (js, name, waitMs, tag) => {
      await evalJs(js);
      await sleep(waitMs);
      if (tag) await dump(tag);
      if (name) await shot(name);
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1600, height: 1000, deviceScaleFactor: 1.5, mobile: false });
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(3500);

    // 1 主界面（任务列表 + 新建任务 Composer）
    await nav(`switchTab("tasks"); document.body.classList.remove("settings-mode");`, "home", 1500, "home");

    // 2/3 运行详情：连载（步骤时间线 + 蜂巢）
    const [runA, runB] = RUNS.split(",");
    if (runA) {
      await nav(`switchTab("runs"); openRun(${JSON.stringify(runA)});
                 (document.querySelector('#rd-tabs .rd-tab[data-tab="steps"]')||{click(){}}).click();`,
      "run_steps", 2800, "run-steps");
      await nav(`(document.querySelector('#rd-tabs .rd-tab[data-tab="hive"]')||{click(){}}).click();`, "hive", 2800, "hive");
    }
    // 4 智能体管理
    await nav(`switchTab("agents");`, "agents", 3500, "agents");
    // 5 模型接入
    await nav(`switchTab("models");`, "models", 2500, "models");
    // 6 CLI 绑定
    await nav(`switchTab("bindings");`, "bindings", 2500, "bindings");
    // 7 用量统计
    await nav(`switchTab("usage");`, "usage", 2500, "usage");
    // 8 经验库
    await nav(`switchTab("skills");`, "skills", 2000, "skills");
    // 9 插件市场
    await nav(`switchTab("market");`, "market", 4500, "market");
    // 10 代码任务完成报告
    if (runB) {
      await nav(`switchTab("runs"); openRun(${JSON.stringify(runB)});
                 (document.querySelector('#rd-tabs .rd-tab[data-tab="report"]')||{click(){}}).click();`,
      "report", 2800, "report");
    }
    // 11 知识库
    await nav(`switchTab("knowledge");`, "knowledge", 2200, "knowledge");
    // 12 自动化
    await nav(`switchTab("automation");`, "automation", 2200, "automation");
    // 13 帮助中心（多章弹层，落在快速上手章）
    await evalJs(`welcomeOpen({ topic: "quickstart" });`);
    await sleep(1800);
    console.log("[help-diag]", await evalJs(`(() => { const w = $("welcome");
      return JSON.stringify({ cls: w.className, vis: !w.classList.contains("hidden"),
        head: w.innerText.slice(0, 150).split("\\n").join(" | ") }); })()`));
    await shot("help");
    await evalJs(`welcomeClose();`);
    await sleep(300);
    // 14 作品信息（连载任务 · 七猫建书资料）
    if (runA) {
      await evalJs(`switchTab("runs"); openRun(${JSON.stringify(runA)});`);
      await sleep(2400);
      await evalJs(`(document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]')||{click(){}}).click();`);
      await sleep(2200);
      console.log("[bookmeta-diag]", await evalJs(`(() => {
        const b = document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]');
        const p = document.querySelector("#rd-pane-bookmeta");
        return JSON.stringify({ tab: !!b, sel: b && b.getAttribute("aria-selected"),
          pane: p && !p.classList.contains("hidden"),
          head: p ? p.innerText.slice(0, 150).split("\\n").join(" | ") : "NO-PANE" }); })()`));
      await shot("bookmeta");
    }
    ws.close();
  } finally {
    proc.kill();
    await sleep(800);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
  process.exit(0);
}
main().catch((e) => { console.error(e); process.exit(1); });
