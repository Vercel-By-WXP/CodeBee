/* 顶栏语言快捷切换 UI 验证：Edge headless + CDP（零依赖，沿 ui_check_about.mjs 模板）。
 * 自起临时服务（TUTTI_DATA=临时目录、端口 18801）→ 断言顶栏地球按钮存在 →
 * 点开下拉含母语两项（中文/English）→ 当前语言带 .on → 点 English 即时切换
 * （data-lang/title/localStorage 记忆）→ 再开下拉选中态正确 → 点外面收起 →
 * Escape 收起 → 切回中文 → 控制台无 JS 错误 → 清理进程。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18801;   // 刻意避开惯用的 18798（Windows 双绑陷阱）
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9343; // 避开 ui_check_about 的 9337，防并行互驱
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-lang-"));

  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪（18801，TUTTI_DATA 隔离）", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  const consoleErrors = [];
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
      if (msg.method === "Runtime.consoleAPICalled" && msg.params?.type === "error") {
        consoleErrors.push(String(msg.params.args?.[0]?.value || "").slice(0, 120));
      }
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

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    // 1) 顶栏地球按钮：存在、在 top-actions 内、SVG 图标、与皮肤按钮相邻
    const btn = JSON.parse(await evalJs(`JSON.stringify({
      has: !!document.getElementById("btn-lang"),
      inTop: !!document.querySelector(".top-actions .lang-wrap #btn-lang"),
      icon: !!document.querySelector("#btn-lang use[href='#i-globe']"),
      title: document.getElementById("btn-lang")?.title || ""
    })`));
    check("顶栏有地球语言按钮（#i-globe，top-actions 内）", btn.has && btn.inTop && btn.icon, JSON.stringify(btn));
    check("按钮 title 双语可读（语言 / Language）", /语言/.test(btn.title) && /Language/i.test(btn.title), btn.title);

    // 2) 下拉初始隐藏；点开后含母语两项且当前语言带 .on
    const hidden0 = await evalJs(`document.getElementById("lang-menu").classList.contains("hidden")`);
    check("下拉初始隐藏", hidden0 === true);
    await evalJs(`toggleLangMenu(); "ok"`);
    await sleep(200);
    const menu = JSON.parse(await evalJs(`JSON.stringify({
      open: !document.getElementById("lang-menu").classList.contains("hidden"),
      zh: (document.querySelector("#lang-menu [data-lang='zh']") || {}).textContent?.trim() || "",
      en: (document.querySelector("#lang-menu [data-lang='en']") || {}).textContent?.trim() || "",
      zhOn: document.querySelector("#lang-menu [data-lang='zh']")?.classList.contains("on"),
      enOn: document.querySelector("#lang-menu [data-lang='en']")?.classList.contains("on")
    })`));
    check("点地球弹出下拉", menu.open === true);
    check("两项母语文案：中文 / English（不做 i18n）", menu.zh === "中文" && menu.en === "English", JSON.stringify(menu));
    check("默认中文带选中态", menu.zhOn === true && menu.enOn === false);

    // 3) 点 English：即时切换（data-lang / 标题 / localStorage / 下拉收起）
    await evalJs(`pickLang("en"); "ok"`);
    await sleep(600);
    const en = JSON.parse(await evalJs(`JSON.stringify({
      lang: document.documentElement.dataset.lang,
      stored: localStorage.getItem("orch.lang"),
      title: document.title,
      menuClosed: document.getElementById("lang-menu").classList.contains("hidden"),
      sample: document.getElementById("page-title").textContent
    })`));
    check("点 English 即时切英文（data-lang=en）", en.lang === "en", JSON.stringify(en));
    check("localStorage 记忆 orch.lang=en", en.stored === "en", en.stored);
    check("浏览器标题变英文", /Multi-agent/.test(en.title), en.title);
    check("英文标题保留当前端口供桌宠复用", en.title.includes("[" + PORT + "]"), en.title);
    check("选完下拉自动收起", en.menuClosed === true);
    check("静态文案已翻（页签标题）", /Task|Tasks/i.test(en.sample), en.sample);

    // 4) 再开下拉：English 项带 .on；皮肤页分段同步（同真源）
    await evalJs(`toggleLangMenu(); "ok"`);
    await sleep(200);
    const on = JSON.parse(await evalJs(`JSON.stringify({
      enOn: document.querySelector("#lang-menu [data-lang='en']")?.classList.contains("on"),
      zhOn: document.querySelector("#lang-menu [data-lang='zh']")?.classList.contains("on")
    })`));
    check("重开下拉选中态在 English", on.enOn === true && on.zhOn === false, JSON.stringify(on));
    await evalJs(`switchTab("settings"); "ok"`);
    await sleep(400);
    const skinSeg = await evalJs(`(document.querySelector("#lang-mode [data-lang].active") || {}).dataset?.lang`);
    check("皮肤页语言分段与顶栏同真源（active=en）", skinSeg === "en", skinSeg);

    // 5) 点外面收起
    await evalJs(`document.getElementById("page-title").click(); "ok"`);
    await sleep(200);
    const closedOut = await evalJs(`document.getElementById("lang-menu").classList.contains("hidden")`);
    check("点外面收起下拉", closedOut === true);

    // 6) Escape 收起
    await evalJs(`toggleLangMenu(); "ok"`);
    await sleep(150);
    await evalJs(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); "ok"`);
    await sleep(200);
    const closedEsc = await evalJs(`document.getElementById("lang-menu").classList.contains("hidden")`);
    check("Escape 收起下拉", closedEsc === true);

    // 7) 切回中文：标题还原、记忆还原
    await evalJs(`pickLang("zh"); "ok"`);
    await sleep(600);
    const zh = JSON.parse(await evalJs(`JSON.stringify({
      lang: document.documentElement.dataset.lang,
      stored: localStorage.getItem("orch.lang"),
      title: document.title
    })`));
    check("切回中文（data-lang=zh + 记忆）", zh.lang === "zh" && zh.stored === "zh", JSON.stringify(zh));
    check("标题还原中文", /多智能体/.test(zh.title), zh.title);
    check("中文标题保留当前端口供桌宠复用", zh.title.includes("[" + PORT + "]"), zh.title);

    check("浏览器控制台无 JS 错误", consoleErrors.length === 0, consoleErrors.join(" | "));
    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 顶栏语言切换（UI/CDP）：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
