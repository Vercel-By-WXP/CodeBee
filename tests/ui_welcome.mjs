/* 帮助中心（首启欢迎引导升级版）UI 验证：自起临时服务 + Edge headless。
 * 覆盖：首启自动弹出（徽章首次启动）、关闭才记账 orch.welcomed、主按钮直达「模型接入」、
 * 刷新后不再弹、「关于与更新」的「使用引导」可重开、Esc / 点遮罩可关、
 * 英文词条命中、章节导航（目录 7 项 / 切章 / 四章实文）、就地帮助问号直达对应章。
 * 结束清理浏览器/服务进程、临时目录。 */
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18902;
const SERVICE = "http://127.0.0.1:" + PORT;
/* CDP 口默认随机（20000-39999）：固定口在多代理并行跑同款测试时会收敛到同一口，
 * Windows 又允许 IPv4/IPv6 双绑——后起的一方会连上别人的浏览器，读到别人的
 * profile（orch.welcomed 已有值 → 首启组断言假红）。TUTTI_TEST_CDP 可显式钉死。 */
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || (20000 + Math.floor(Math.random() * 20000)));
console.log("（CDP 口：%s）", CDP_PORT);
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
    check("① 首启徽章显示「首次启动」",
      (await evalJs(`document.getElementById("welcome-badge").textContent`)) === "首次启动");
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
    check("④ 关于页「帮助中心」可重开", replay === true);

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

    // ⑦ 命令面板（Ctrl+K 菜单）也含「帮助中心」入口，点击可开
    await evalJs(`cmdkOpen(); "ok"`);
    await sleep(300);
    const cmdItem = `Array.from(document.querySelectorAll("#cmdk-list .cmdk-item"))
      .find(x => x.textContent.indexOf("帮助中心") >= 0)`;
    check("⑦ 命令面板含「帮助中心」入口",
      (await evalJs(`!!(${cmdItem})`)) === true);
    await evalJs(`(${cmdItem} || {}).click?.(); "ok"`);
    await sleep(300);
    check("⑦ 点命令面板项打开帮助中心",
      (await evalJs(`!document.getElementById("welcome").classList.contains("hidden")`)) === true);
    await evalJs(`welcomeClose(); "ok"`);

    // ⑧ 设置导航「帮助中心」入口（软件分组，火箭图标；点击弹层不切子页）
    await evalJs(`switchTab("about"); "ok"`);
    await sleep(400);
    const navGuide = JSON.parse(await evalJs(`JSON.stringify({
      has: !!document.getElementById("btn-guide"),
      icon: !!document.querySelector("#btn-guide use[href='#i-rocket']"),
      label: ((document.getElementById("btn-guide") || {}).textContent || "").trim()
    })`));
    check("⑧ 设置导航「帮助中心」项存在（火箭图标）",
      navGuide.has && navGuide.icon && navGuide.label.indexOf("帮助中心") >= 0, JSON.stringify(navGuide));
    await evalJs(`document.getElementById("btn-guide").click(); "ok"`);
    await sleep(300);
    const afterNav = JSON.parse(await evalJs(`JSON.stringify({
      welcome: !document.getElementById("welcome").classList.contains("hidden"),
      stillAbout: !document.getElementById("sub-about").classList.contains("hidden")
    })`));
    check("⑧ 点「帮助中心」打开弹层且不切子页",
      afterNav.welcome && afterNav.stillAbout, JSON.stringify(afterNav));
    await evalJs(`welcomeClose(); "ok"`);

    // ⑫ F1 快捷键打开帮助中心（再按 Esc 关闭）
    await evalJs(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "F1" })); "ok"`);
    await sleep(200);
    const f1 = JSON.parse(await evalJs(`JSON.stringify({
      open: !document.getElementById("welcome").classList.contains("hidden"),
      badge: document.getElementById("welcome-badge").textContent
    })`));
    check("⑫ F1 打开帮助中心（徽章=帮助中心）",
      f1.open === true && f1.badge === "帮助中心", JSON.stringify(f1));
    await evalJs(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); "ok"`);
    await sleep(200);
    check("⑫ Esc 再关",
      (await evalJs(`document.getElementById("welcome").classList.contains("hidden")`)) === true);

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
    check("⑨ 英文词条命中（手动打开：标题/徽章=Help center，主按钮=Set up models）",
      en.title === "Help center" && en.btn === "Set up models" && en.badge === "Help center",
      JSON.stringify(en));

    // ⑩ 帮助中心章节导航（中文）：目录 7 项、默认快速上手、章切换、整理中占位
    await evalJs(`localStorage.setItem("orch.lang","zh"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    await waitFor(`typeof S === "object" && S !== null && S.state !== null`);
    await evalJs(`welcomeOpen(); "ok"`);
    await sleep(300);
    const toc1 = JSON.parse(await evalJs(`JSON.stringify({
      n: document.querySelectorAll("#help-toc .hitem").length,
      first: (document.querySelector("#help-toc .hitem.active") || { dataset: {} }).dataset.ch || "",
      badge: document.getElementById("welcome-badge").textContent
    })`));
    check("⑩ 目录 8 项、默认落在快速上手、徽章「帮助中心」",
      toc1.n === 8 && toc1.first === "quickstart" && toc1.badge === "帮助中心", JSON.stringify(toc1));
    await evalJs(`helpGo("models"); "ok"`);
    await sleep(200);
    const chModels = JSON.parse(await evalJs(`JSON.stringify({
      active: (document.querySelector("#help-toc .hitem.active") || { dataset: {} }).dataset.ch || "",
      body: document.getElementById("help-body").textContent
    })`));
    check("⑩ 「模型接入与绑定」章：正文含一键推荐绑定/报错速查",
      chModels.active === "models" && chModels.body.indexOf("一键推荐绑定") >= 0 &&
        chModels.body.indexOf("报错速查") >= 0,
      String(chModels.body).slice(0, 80));
    const ch5 = JSON.parse(await evalJs(`JSON.stringify(
      ["serial", "publish", "code", "auto", "faq"].map((id) => {
        helpGo(id);
        return { id, txt: document.getElementById("help-body").textContent };
      })
    )`));
    const kw = { serial: "大纲", publish: "番茄", code: "待裁决", auto: "定时", faq: "自动续跑" };
    check("⑩ 连载/发布/代码/自动化/FAQ 五章均为实文（无整理中占位）",
      ch5.every((c) => c.txt.indexOf(kw[c.id]) >= 0 && c.txt.indexOf("正在整理") < 0),
      JSON.stringify(ch5.map((c) => ({ id: c.id, head: String(c.txt).slice(0, 30) }))));
    check("⑩ auto 章含禅道小节",
      ch5.find((c) => c.id === "auto").txt.indexOf("禅道") >= 0);
    await evalJs(`helpGo("features"); "ok"`);
    await sleep(200);
    const featsTxt = String(await evalJs(`document.getElementById("help-body").textContent`));
    check("⑩ 功能一览章含插件市场与经验库条目",
      featsTxt.indexOf("插件市场") >= 0 && featsTxt.indexOf("经验库") >= 0);
    await evalJs(`welcomeClose(); "ok"`);

    // ⑪ 就地帮助问号：设置子页 .hhelp（事件委托）直达帮助中心对应章
    await evalJs(`switchTab("models"); "ok"`);
    await sleep(400);
    const hints = JSON.parse(await evalJs(`JSON.stringify({
      models: !!document.querySelector('#sub-models .hhelp[data-help-topic="models"]'),
      bindings: !!document.querySelector('#sub-bindings .hhelp[data-help-topic="models"]'),
      auto: !!document.querySelector('#sub-automation .hhelp[data-help-topic="auto"]'),
      market: !!document.querySelector('#sub-market .hhelp[data-help-topic="auto"]'),
      zentao: !!document.querySelector('#sub-zentao .hhelp[data-help-topic="auto"]'),
      usage: !!document.querySelector('#sub-usage .hhelp[data-help-topic="auto"]'),
      agents: !!document.querySelector('#sub-agents .hhelp[data-help-topic="models"]'),
      skills: !!document.querySelector('#sub-skills .hhelp[data-help-topic="features"]'),
      hive: !!document.querySelector('#rd-hive .hhelp[data-help-topic="serial"]')
    })`));
    check("⑪ 九处问号就位（models/bindings/自动化/市场/禅道/用量/目录/经验库/蜂巢）",
      hints.models && hints.bindings && hints.auto && hints.market && hints.zentao && hints.usage && hints.agents && hints.skills && hints.hive, JSON.stringify(hints));
    const probe = JSON.parse(await evalJs(`JSON.stringify((() => {
      const b = document.querySelector('#sub-models .hhelp');
      if (!b) return { open: false, ch: "", badge: "" };
      b.click();
      return {
        open: !document.getElementById("welcome").classList.contains("hidden"),
        ch: (document.querySelector("#help-toc .hitem.active") || { dataset: {} }).dataset.ch || "",
        badge: document.getElementById("welcome-badge").textContent
      };
    })())`));
    check("⑪ 点问号打开帮助中心并落到对应章（徽章=帮助中心）",
      probe.open === true && probe.ch === "models" && probe.badge === "帮助中心", JSON.stringify(probe));
    await evalJs(`welcomeClose(); "ok"`);

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
