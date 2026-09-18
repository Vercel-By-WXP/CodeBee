/* 手机抽屉 + 图标点击回归：窄屏下点图标（svg/use 本身）能否命中按钮、进设置、自动收起抽屉。
 * 这条路径是新增 SVG 图标后的风险点：点击目标从文字变成 <svg>/<use>，
 * 侧栏的 collapseDrawerIfMobile 依赖 e.target.closest("button")。
 * 用法：先起临时服务（tests/ui_check.mjs 顶部的 SERVICE），再 node tests/ui_mobile.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SERVICE = process.env.SERVICE || "http://127.0.0.1:18798";   // 18798 常被并行 agent 双绑，可用 SERVICE 覆盖
const CDP_PORT = Number(process.env.CDP_PORT) || 9335;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 240)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-mob-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=390,844", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);

    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true });
      return r.result?.result?.value;
    };
    const clickAt = async (sel) => {
      const box = JSON.parse(await evalJs(`(() => {
        const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();
        return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
      })()`));
      for (const type of ["mousePressed", "mouseReleased"]) {
        await send("Input.dispatchMouseEvent", { type, x: box.x, y: box.y, button: "left", clickCount: 1 });
      }
    };

    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    // 首次启动欢迎引导会盖住真实触摸目标：记账关掉（引导自身由 ui_welcome.mjs 覆盖）
    await evalJs(`localStorage.setItem("orch.welcomed","1"); try { welcomeClose(); } catch (e) {} "ok"`);

    check("窄屏默认收起抽屉", await evalJs(`document.body.classList.contains("side-collapsed")`) === true);

    // 关键点：点击目标是 <use> 子元素，closest("button") 仍须命中
    const closest = await evalJs(`(() => {
      const use = document.querySelector("#btn-settings use");
      return use && use.closest("button") ? use.closest("button").id : "none";
    })()`);
    check("从 svg <use> 能 closest 到按钮（抽屉收起依赖此行为）", closest === "btn-settings", String(closest));

    // 展开抽屉 → 真实点齿轮图标 → 应进设置页且抽屉收回
    await evalJs(`document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(400);
    await clickAt("#btn-settings use");
    await sleep(700);
    const afterGear = JSON.parse(await evalJs(`JSON.stringify({
      collapsed: document.body.classList.contains("side-collapsed"),
      settingsMode: document.body.classList.contains("settings-mode"),
      menuHidden: document.getElementById("ctx-menu").classList.contains("hidden")
    })`));
    check("点齿轮图标即进设置页且抽屉自动收起",
      afterGear.collapsed && afterGear.settingsMode && afterGear.menuHidden, JSON.stringify(afterGear));

    // 展开 → 点某个设置导航项 → 切页并收起
    await evalJs(`document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(400);
    await clickAt('.set-item[data-sub="bindings"] use');
    await sleep(800);
    const afterNav = JSON.parse(await evalJs(`JSON.stringify({
      collapsed: document.body.classList.contains("side-collapsed"),
      title: document.getElementById("page-title").textContent
    })`));
    check("点设置导航图标切换子页并收起抽屉",
      afterNav.collapsed && afterNav.title === "CLI 绑定", JSON.stringify(afterNav));

    // 手机连接图标同理（先回任务视图：设置模式下 .side-main 整体隐藏，该图标不可点）
    await evalJs(`document.getElementById("btn-set-back").click(); document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(500);
    check("返回任务视图后手机连接图标可点",
      await evalJs(`getComputedStyle(document.querySelector(".side-main")).display`) === "flex");
    await clickAt("#btn-phone-side use");
    await sleep(600);
    const afterPhone = JSON.parse(await evalJs(`JSON.stringify({
      collapsed: document.body.classList.contains("side-collapsed"),
      modal: !document.getElementById("modal").classList.contains("hidden"),
      title: document.getElementById("modal-title").textContent
    })`));
    check("点手机连接图标弹出扫码框并收起抽屉",
      afterPhone.collapsed && afterPhone.modal && /手机连接/.test(afterPhone.title), JSON.stringify(afterPhone));

    // 设置模式下的「手机连接」导航项也应是弹框、不切页
    await evalJs(`closeModal(); document.getElementById("btn-settings").click(); "ok"`);
    await sleep(700);
    await evalJs(`document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(300);
    await clickAt('.set-item[data-sub="__phone"] use');
    await sleep(600);
    const viaNav = JSON.parse(await evalJs(`JSON.stringify({
      modal: !document.getElementById("modal").classList.contains("hidden"),
      title: document.getElementById("page-title").textContent
    })`));
    check("设置导航里的手机连接走弹框（不切子页）",
      viaNav.modal && viaNav.title === "CLI 绑定", JSON.stringify(viaNav));

    /* 编排者供应商指示：窄屏抽屉里也要可用，且文字过长不能撑破侧栏 */
    await evalJs(`closeModal(); exitSettings(); document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(700);
    const provGeo = JSON.parse(await evalJs(`(() => {
      const b = document.getElementById("btn-prov-side");
      const nm = document.getElementById("prov-side-text");
      const side = document.getElementById("sidebar").getBoundingClientRect();
      const r = b.getBoundingClientRect();
      return JSON.stringify({
        visible: r.width > 0 && r.height > 0,
        w: Math.round(r.width),
        text: nm.textContent.trim(),
        noOverflow: nm.scrollWidth <= nm.clientWidth + 1,
        insideSidebar: r.left >= side.left - 1 && r.right <= side.right + 1,
        gearLeft: document.getElementById("btn-settings").getBoundingClientRect().right <= r.left + 1,
        phoneRight: r.right <= document.getElementById("btn-phone-side").getBoundingClientRect().left + 1,
      });
    })()`));
    check("窄屏抽屉里供应商指示可见且夹在两图标之间",
      provGeo.visible && provGeo.w > 40 && provGeo.gearLeft && provGeo.phoneRight && provGeo.insideSidebar,
      JSON.stringify(provGeo));
    check("指示文字不外溢（超长会省略号截断）", provGeo.noOverflow, JSON.stringify(provGeo));

    await clickAt("#btn-prov-side span.nm");
    await sleep(900);
    const provNav = JSON.parse(await evalJs(`JSON.stringify({
      collapsed: document.body.classList.contains("side-collapsed"),
      active: (document.querySelector(".set-item.active") || {}).dataset?.sub || ""
    })`));
    check("点供应商指示进编排设置并收起抽屉",
      provNav.collapsed && provNav.active === "orch", JSON.stringify(provNav));

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((x) => !x).length;
  console.log("\n===== 手机抽屉/图标点击：%d 通过 / %d 失败 =====", results.length - bad, bad);
  if (bad) process.exit(1);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
