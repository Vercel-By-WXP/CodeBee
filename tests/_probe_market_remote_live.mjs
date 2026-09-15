/* 外部目录·真实数据 UI 探针：先由 Python 侧对五源真实刷新落缓存（临时数据
 * 目录），本脚本起临时服务 + Edge headless CDP，验证市场页对真实 620 条目录的
 * 渲染：来源下拉计数、卡片规模、灰显规模、搜索、来源筛选、截断提示。
 * 依赖外网（clawhub.ai / jsdelivr），不在常规测试套件里跑。
 * 用法：先跑 tests/_probe_market_remote_seed.py 拿到数据目录，再：
 *   node tests/_probe_market_remote_live.mjs <dataDir> */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18791;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9343;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = process.argv[2];
if (!dataDir) { console.error("用法: node _probe_market_remote_live.mjs <dataDir>"); process.exit(2); }

const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
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
      try { up = (await (await fetch(SERVICE + "/api/state")).text()).length > 0; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    const remote = await fetch(SERVICE + "/api/market/remote").then((r) => r.json());
    if (remote.total === 0) {
      console.log("  ⚠ total=0：多半撞了端口双绑（Windows SO_REUSEADDR），"
        + "用 netstat 查 " + PORT + " 的 PID 归属后按 PID 清理再试");
    }
    check("API 五源目录就绪（total>500）", remote.total > 500, "total=" + remote.total);
    check("每源限 60 下发", remote.entries.length <= 5 * 60 + 5 && remote.entries.length > 100,
      "下发=" + remote.entries.length);

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(dataDir, "..", "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
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
    check("Edge headless 启动", !!target);
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
    await evalJs(`switchTab("market"); "ok"`);
    await sleep(800);
    await evalJs(`mkSetView("remote"); "ok"`);
    await sleep(1200);

    // 来源下拉：真实计数进标签（条数会随上游实时变动，只断言形态）
    const srcLabels = await evalJs(`[...document.getElementById("mkr-source").options].map(o => o.textContent).join("|")`);
    check("下拉含 ClawHub 与计数", /ClawHub（OpenClaw 生态）（\d+）/.test(srcLabels) && !/ClawHub（OpenClaw 生态）（0）/.test(srcLabels), srcLabels);
    check("下拉含 Anthropic 生态（296）", /Anthropic 生态（296）/.test(srcLabels), srcLabels);

    // 卡片规模与灰显规模（每源 60 配额下灰显占比被稀释，只要求成规模出现）
    const cards = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card").length`);
    check("真实目录渲染 200+ 卡片", cards >= 200, "cards=" + cards);
    const gray = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card.blocked").length`);
    check("真实不适配条目灰显成规模", gray >= 20, "gray=" + gray + "/" + cards);
    const installBtns = await evalJs(`[...document.querySelectorAll("#mkr-grid .mk-card button.primary")].length`);
    check("可安装按钮若干", installBtns > 20, "install=" + installBtns);

    // 截断提示
    const meta = await evalJs(`document.getElementById("mkr-meta").textContent`);
    check("元信息含更新时间与截断提示", meta.includes("目录更新于") && meta.includes("60 条"), meta.slice(0, 120));

    // 真实搜索：commit 应有多条
    await evalJs(`(() => { const s = document.getElementById("mkr-search");
      s.value = "commit"; s.dispatchEvent(new Event("input")); })()`);
    await sleep(500);
    const cntSearch = await evalJs(`document.getElementById("mk-count").textContent`);
    check("真实搜索 commit 有结果", !cntSearch.includes("共 0"), cntSearch);
    await evalJs(`(() => { const s = document.getElementById("mkr-search"); s.value = ""; s.dispatchEvent(new Event("input")); })()`);
    await sleep(300);

    // ClawHub 来源筛选：恰好 60
    await evalJs(`(() => { const s = document.getElementById("mkr-source"); s.value = "clawhub"; s.dispatchEvent(new Event("change")); })()`);
    await sleep(500);
    const cntClaw = await evalJs(`document.getElementById("mk-count").textContent`);
    check("筛选 ClawHub 计数 60", cntClaw.includes("60"), cntClaw);

    await shot("market-remote-live.png");
    console.log("\n截图: .ui-shots/market-remote-live.png");
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n== 结果: " + (results.length - bad.length) + "/" + results.length + " 通过 ==");
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
