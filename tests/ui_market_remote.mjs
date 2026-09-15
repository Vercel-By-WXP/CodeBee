/* 外部目录 UI 验证：市场页本地/外部视图切换 / 外部条目渲染 / 来源筛选 /
 * 不适配灰显（脚本·钩子·MCP 预分类）/ 安装失败 toast（SSRF 拦本地地址，确定性
 * 失败不外联）/ 卸载入口。自起临时服务（预置目录缓存种子）→ Edge headless
 * + CDP 断言 → 截图，结束清理进程与临时目录。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18797;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9341;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function seedRemoteCatalog(dataDir) {
  // 4 个条目：1 个纯技能可装（装的话会打 127.0.0.1 → SSRF 网关确定性拒绝，
  // 不碰真网络）；3 个预分类灰显（mcp / hooks / 不认识的来源类型）
  const zipSrc = (name) => ({ source: "url", type: "zip", url: "https://127.0.0.1:9/" + name + ".zip" });
  const plugins = [
    { name: "clean-commit", description: "A pure skill plugin for commit messages.",
      version: "0.1.0", author: { name: "Eco" }, category: "developer-tools",
      keywords: ["git"], source: zipSrc("clean") },
    { name: "mcp-tool", description: "Talks to servers.",
      author: { name: "Eco" }, category: "developer-tools",
      keywords: ["mcp"], source: zipSrc("mcp") },
    { name: "hook-thing", description: "Lifecycle hooks.",
      author: { name: "Eco" }, category: "productivity",
      keywords: ["hooks"], source: zipSrc("hook") },
    { name: "local-only", description: "Weird source.",
      author: { name: "Eco" }, category: "design",
      source: { source: "local", path: "x" } },
  ];
  const cache = join(dataDir, "market_remote");
  mkdirSync(cache, { recursive: true });
  writeFileSync(join(cache, "zcode.json"), JSON.stringify({
    fetched_at: "2026-09-15 10:00:00",
    url: "https://cdn.example.com/marketplace.json",
    catalog: { name: "zcode-plugins-official", description: "seed", plugins },
  }, null, 1), "utf8");
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uimkt-"));
  const dataDir = join(tmp, "data");
  seedRemoteCatalog(dataDir);

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

    // 服务端直读 API：预分类与 installed 布尔
    const remote0 = await fetch(SERVICE + "/api/market/remote").then((r) => r.json());
    check("API 外部目录 4 条且 3 条预分类 blocked",
      remote0.total === 4 && remote0.entries.filter((e) => e.compat === "blocked").length === 3,
      JSON.stringify(remote0).slice(0, 160));
    check("API 未安装态 installable=false",
      remote0.entries.every((e) => e.installable === (e.compat === "ok")),
      JSON.stringify(remote0.entries.map((e) => [e.name, e.installable])));

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
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
    check("Edge headless 启动并开放 CDP", !!target);
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

    // 进插件市场页
    await evalJs(`switchTab("market"); "ok"`);
    await sleep(1000);

    // 1) 默认本地视图：视图切换钮存在、本地卡片在、外部区隐藏
    const localCards = await evalJs(`document.querySelectorAll("#mk-grid .mk-card").length`);
    check("本地视图默认可见且有卡片", localCards > 0, "cards=" + localCards);
    check("外部区默认隐藏", await evalJs(`document.getElementById("mk-remote").classList.contains("hidden")`));

    // 2) 切到外部目录：卡片 4 张、来源徽章、计数、更新时间
    await evalJs(`mkSetView("remote"); "ok"`);
    await sleep(400);
    check("切换后外部区可见", await evalJs(`!document.getElementById("mk-remote").classList.contains("hidden")`));
    check("本地区随之隐藏", await evalJs(`document.getElementById("mk-local").classList.contains("hidden")`));
    const remoteCards = await evalJs(`document.querySelectorAll("#mkr-grid .mk-card").length`);
    check("外部目录 4 张卡片", remoteCards === 4, "cards=" + remoteCards);
    const cnt = await evalJs(`document.getElementById("mk-count").textContent`);
    check("计数显示共 4 个", cnt.includes("4"), cnt);
    const meta = await evalJs(`document.getElementById("mkr-meta").textContent`);
    check("元信息含更新时间", meta.includes("2026-09-15"), meta);
    const badges = await evalJs(`[...document.querySelectorAll("#mkr-grid .mk-src")].map(x => x.textContent)`);
    check("卡片带来源徽章（ZCode 官方 ×4）", badges.length === 4 && badges.every((b) => b.includes("ZCode")), badges.join("|"));
    const srcOpts = await evalJs(`document.getElementById("mkr-source").options.length`);
    const srcLabels = await evalJs(`[...document.getElementById("mkr-source").options].map(o => o.textContent).join("|")`);
    check("来源下拉=全部+5 个已知源（未拉取的计数 0 也列出）",
      srcOpts === 6 && srcLabels.includes("ZCode 官方") && srcLabels.includes("ClawHub"),
      srcLabels);

    // 3) 不适配灰显：3 个禁用按钮 + 卡片降透明 + title 原因
    const gray = await evalJs(`(() => {
      const btns = [...document.querySelectorAll("#mkr-grid .mk-card button[disabled]")];
      return { n: btns.length, txt: btns[0] && btns[0].textContent.trim(),
               title: btns.find(b => (b.title || "").includes("MCP")) ? 1 : 0,
               dim: document.querySelectorAll("#mkr-grid .mk-card.blocked").length };
    })()`);
    check("3 个不适配按钮灰显禁用", gray.n === 3 && gray.txt.includes("不适配"), JSON.stringify(gray));
    check("含 MCP 原因的 title 提示", gray.title === 1);
    check("3 张卡片降透明样式", gray.dim === 3, "dim=" + gray.dim);
    const hasInstall = await evalJs(`[...document.querySelectorAll("#mkr-grid .mk-card button.primary")].length`);
    check("仅 1 个可安装按钮", hasInstall === 1, "install=" + hasInstall);

    // 4) 来源筛选：选 ZCode 官方后仍 4 条（唯一来源），计数不变
    await evalJs(`(() => { const s = document.getElementById("mkr-source"); s.value = "zcode"; s.dispatchEvent(new Event("change")); })()`);
    await sleep(300);
    const cnt2 = await evalJs(`document.getElementById("mk-count").textContent`);
    check("按来源筛选后计数仍 4", cnt2.includes("4"), cnt2);

    // 5) 搜索过滤：搜 clean 只剩 1
    await evalJs(`(() => { const s = document.getElementById("mkr-search");
      s.value = "clean"; s.dispatchEvent(new Event("input")); })()`);
    await sleep(300);
    const cnt3 = await evalJs(`document.getElementById("mk-count").textContent`);
    check("搜索 clean 后计数 1", cnt3.includes("1"), cnt3);
    await evalJs(`(() => { const s = document.getElementById("mkr-search"); s.value = ""; s.dispatchEvent(new Event("input")); })()`);

    // 6) 安装失败路径：可装条目的 zip 地址指向 127.0.0.1 → SSRF 网关确定性拒绝，
    //    toast 报错（验证整条 安装→服务端→错误提示 链路，且不外联）
    await evalJs(`mkrInstall("remote-zcode-clean-commit"); "ok"`);
    await sleep(1500);
    const toastTxt = await evalJs(`(document.getElementById("toast")||{textContent:""}).textContent`);
    check("安装被 SSRF 拦截并 toast 报错", /拒绝|非公网|失败/.test(toastTxt), toastTxt);

    // 7) 切回本地视图：布局还原
    await evalJs(`mkSetView("local"); "ok"`);
    await sleep(300);
    check("切回本地视图外部区隐藏", await evalJs(`document.getElementById("mk-remote").classList.contains("hidden")`));
    check("本地卡片仍在", (await evalJs(`document.querySelectorAll("#mk-grid .mk-card").length`)) > 0);

    await shot("market-remote.png");
    console.log("\n截图: .ui-shots/market-remote.png");
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n== 结果: " + (results.length - bad.length) + "/" + results.length + " 通过 ==");
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
