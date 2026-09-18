/* 首次启动欢迎引导 UI 验证：自起临时服务 + Edge headless。
 * 覆盖：首启自动弹出、关闭才记账 orch.welcomed、主按钮直达「模型接入」、
 * 刷新后不再弹、「关于与更新」的「使用引导」可重开、Esc / 点遮罩可关、
 * 英文词条命中（data-i18n 应用）。
 * 结束清理浏览器/服务进程、临时目录。 */
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18902;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9362);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uiwelcome-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });

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
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE, [
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
    const evalJs = async (expr, awaitPromise = false) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise });
      return r.result?.result?.value;
    };
    const waitFor = async (expr, ms = 20000) => {
      const wrapped = typeof expr === "function" ? "(" + expr.toString() + ")()" : expr;
      for (let t = 0; t < ms; t += 400) {
        if (await evalJs(wrapped, true)) return true;
        await sleep(400);
      }
      return false;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    const ready = await waitFor(`typeof S === "object" && S !== null && S.state !== null`);
    check("页面数据就绪", ready);

    // ① 首次启动：欢迎引导自动弹出，且此时还没记账
    const wVisible = await evalJs(
      `!document.getElementById("welcome").classList.contains("hidden")`);
    check("① 首启自动弹出欢迎引导", wVisible === true);
    check("① 打开期间未提前记账（关闭才写 orch.welcomed）",
      (await evalJs(`localStorage.getItem("orch.welcomed")`)) === null);

    // ② 主按钮 → 关引导 + 直达「模型接入」设置页
    await evalJs(`document.querySelector("#welcome .wl-btn.primary").click(); "ok"`);
    const gone1 = await evalJs(`document.getElementById("welcome").classList.contains("hidden")`);
    check("② 点「开始配置模型」引导关闭", gone1 === true);
    const nav = JSON.parse(await evalJs(`JSON.stringify({
      settings: document.body.classList.contains("settings-mode"),
      models: !document.getElementById("sub-models").classList.contains("hidden")
    })`));
    check("② 且直达「模型接入」子页", nav.settings && nav.models, JSON.stringify(nav));
    check("② 关闭后记账 orch.welcomed=1",
      (await evalJs(`localStorage.getItem("orch.welcomed")`)) === "1");

    // ③ 刷新：不再自动弹
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    await waitFor(`typeof S === "object" && S !== null && S.state !== null`);
    const wStays = await evalJs(
      `document.getElementById("welcome").classList.contains("hidden")`);
    check("③ 已看过引导：刷新后不再弹", wStays === true);

    // ④ 「关于与更新」页的「使用引导」可重开
    await evalJs(`switchTab("about"); "ok"`);
    await sleep(400);
    await evalJs(`document.getElementById("btn-welcome-replay").click(); "ok"`);
    const replay = await evalJs(
      `!document.getElementById("welcome").classList.contains("hidden")`);
    check("④ 关于页「使用引导」可重开", replay === true);

    // ⑤ Esc 可关
    await evalJs(
      `document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); "ok"`);
    await sleep(200);
    check("⑤ Esc 关闭引导",
      (await evalJs(`document.getElementById("welcome").classList.contains("hidden")`)) === true);

    // ⑥ 点遮罩可关（重开后）
    await evalJs(`welcomeOpen(); "ok"`);
    await sleep(200);
    await evalJs(`document.querySelector("#welcome .welcome-mask").click(); "ok"`);
    await sleep(200);
    check("⑥ 点遮罩关闭引导",
      (await evalJs(`document.getElementById("welcome").classList.contains("hidden")`)) === true);

    // ⑦ 命令面板（Ctrl+K 菜单）也含「使用引导」入口，点击可开
    await evalJs(`cmdkOpen(); "ok"`);
    await sleep(300);
    const cmdItem = `Array.from(document.querySelectorAll("#cmdk-list .cmdk-item"))
      .find(x => x.textContent.indexOf("使用引导") >= 0)`;
    check("⑦ 命令面板含「使用引导」入口",
      (await evalJs(`!!(${cmdItem})`)) === true);
    await evalJs(`(${cmdItem} || {}).click?.(); "ok"`);
    await sleep(300);
    check("⑦ 点命令面板项打开引导",
      (await evalJs(`!document.getElementById("welcome").classList.contains("hidden")`)) === true);
    await evalJs(`welcomeClose(); "ok"`);

    // ⑧ 设置导航「使用引导」入口（软件分组，火箭图标；点击弹层不切子页）
    await evalJs(`switchTab("about"); "ok"`);
    await sleep(400);
    const navGuide = JSON.parse(await evalJs(`JSON.stringify({
      has: !!document.getElementById("btn-guide"),
      icon: !!document.querySelector("#btn-guide use[href='#i-rocket']"),
      label: ((document.getElementById("btn-guide") || {}).textContent || "").trim()
    })`));
    check("⑧ 设置导航「使用引导」项存在（火箭图标）",
      navGuide.has && navGuide.icon && navGuide.label.indexOf("使用引导") >= 0, JSON.stringify(navGuide));
    await evalJs(`document.getElementById("btn-guide").click(); "ok"`);
    await sleep(300);
    const afterNav = JSON.parse(await evalJs(`JSON.stringify({
      welcome: !document.getElementById("welcome").classList.contains("hidden"),
      stillAbout: !document.getElementById("sub-about").classList.contains("hidden")
    })`));
    check("⑧ 点「使用引导」打开欢迎层且不切子页",
      afterNav.welcome && afterNav.stillAbout, JSON.stringify(afterNav));
    await evalJs(`welcomeClose(); "ok"`);

    // ⑨ 英文词条命中：切 en 后标题与主按钮走字典
    await evalJs(`localStorage.setItem("orch.lang","en"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    await waitFor(`typeof S === "object" && S !== null && S.state !== null`);
    await evalJs(`welcomeOpen(); "ok"`);
    await sleep(300);
    const en = JSON.parse(await evalJs(`JSON.stringify({
      title: document.getElementById("welcome-title").textContent.trim(),
      btn: document.querySelector("#welcome .wl-btn.primary span").textContent.trim(),
      badge: document.querySelector(".welcome-badge").textContent.trim()
    })`));
    check("⑦ 英文词条命中（标题/按钮/徽章）",
      en.title === "Welcome to CodeBee" && en.btn === "Set up models" && en.badge === "First launch",
      JSON.stringify(en));

  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) {
      if (p && p.pid) {
        try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
      }
    }
    if (results.some((r) => !r.ok)) {
      console.log("（失败现场保留：%s）", tmp);
    } else {
      try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 首启欢迎引导 UI：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
