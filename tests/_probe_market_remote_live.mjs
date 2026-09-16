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
    check("API 五源目录就绪（过滤后全量 total>500）", remote.total > 500, "total=" + remote.total);
    check("默认每页 60 下发", remote.entries.length === 60 && remote.has_more === true,
      "本页=" + remote.entries.length + " total=" + remote.total);
    const page2 = await fetch(SERVICE + "/api/market/remote?offset=60&limit=60").then((r) => r.json());
    const overlap = page2.entries.filter((e) => remote.entries.some((x) => x.id === e.id));
    check("第二页不重叠", overlap.length === 0 && page2.entries.length === 60, "重叠=" + overlap.length);
    const srcAll = await fetch(SERVICE + "/api/market/remote?source=anthropic").then((r) => r.json());
    check("来源筛选返回全量（不再被配额截断）", srcAll.total > 200 && srcAll.total <= 300,
      "anthropic total=" + srcAll.total);
    const qAll = await fetch(SERVICE + "/api/market/remote?q=commit").then((r) => r.json());
    check("搜索跨全量命中", qAll.total >= 1 && qAll.entries.every(
      (e) => (e.name + e.title + e.desc).toLowerCase().includes("commit")), "commit total=" + qAll.total);

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

    // 来源下拉：真实计数进标签
    const srcLabels = await evalJs(`[...document.getElementById("mkr-source").options].map(o => o.textContent).join("|")`);
    check("下拉含五源与计数", srcLabels.includes("ClawHub") && srcLabels.includes("ZCode 官方")
      && srcLabels.includes("Anthropic 生态"), srcLabels.slice(0, 160));

    // 卡片规模：首屏只渲第一页（60），滚动懒加载续页
    const cards = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card").length`);
    check("首屏渲第一页 60 卡片", cards === 60, "cards=" + cards);
    const gray = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card.blocked").length`);
    check("灰显大幅减少（剥离式：仅 unsupported 来源）", gray <= 20, "gray=" + gray + "/" + cards);
    const installBtns = await evalJs(`[...document.querySelectorAll("#mkr-grid .mk-card button.primary")].length`);
    check("可安装按钮占多数（>2/3）", installBtns >= cards * 2 / 3, "install=" + installBtns + "/" + cards);
    const cntLoad = await evalJs(`document.getElementById("mk-count").textContent`);
    check("计数「已加载 60 / 共 N」", /已加载\s*60\s*\/\s*共\s*\d+/.test(cntLoad), cntLoad);
    check("续页区可见（还有更多）", await evalJs(`!document.getElementById("mkr-more").classList.contains("hidden")`));

    // 真实搜索 commit（服务端跨全量过滤 + 防抖）：结果数应远超本页可见
    await evalJs(`(() => { const s = document.getElementById("mkr-search");
      s.value = "commit"; s.dispatchEvent(new Event("input")); })()`);
    await sleep(1200);
    const cntSearch = await evalJs(`document.getElementById("mk-count").textContent`);
    const cardsSearch = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card").length`);
    check("真实搜索 commit 命中跨全量（>3）", !cntSearch.includes("共 0") && Number((cntSearch.match(/共\s*(\d+)/) || [])[1]) > 3 && cardsSearch > 3,
      cntSearch);
    await evalJs(`(() => { const s = document.getElementById("mkr-search"); s.value = ""; s.dispatchEvent(new Event("input")); })()`);
    await sleep(1200);

    // 滚动懒加载：点「加载更多」→ 卡片变 120，计数同步
    await evalJs(`document.getElementById("mkr-more-btn").click(); "ok"`);
    await sleep(1200);
    const cards2 = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card").length`);
    check("加载更多→120 卡片", cards2 === 120, "cards=" + cards2);
    const cntMore = await evalJs(`document.getElementById("mk-count").textContent`);
    check("计数→已加载 120", cntMore.includes("120"), cntMore);

    // 滚到底：哨兵进视野自动续页
    await evalJs(`document.querySelector("main").scrollTop = document.querySelector("main").scrollHeight; "ok"`);
    await sleep(1500);
    const cards3 = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card").length`);
    check("滚到底自动续页（>120）", cards3 > 120, "cards=" + cards3);

    // 元信息只剩更新时间（截断提示已随分页退役）
    const meta = await evalJs(`document.getElementById("mkr-meta").textContent`);
    check("元信息含更新时间", meta.includes("目录更新于"), meta.slice(0, 120));

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
