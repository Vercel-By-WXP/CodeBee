/* 手机抽屉 + 图标点击回归：窄屏下点图标（svg/use 本身）能否命中按钮、进设置、自动收起抽屉。
 * 这条路径是新增 SVG 图标后的风险点：点击目标从文字变成 <svg>/<use>，
 * 侧栏的 collapseDrawerIfMobile 依赖 e.target.closest("button")。
 * 2026-10-08 同步导航轨改造（b1f1116）后的入口现状：side-foot/side-quick 已隐藏，
 * 设置走 rail 代理按钮、手机连接走设置导航 __phone（供应商指示入口随 side-foot 移除）。
 * 浏览器段迁移到共享 _ui_boot.mjs（DPR 钉 1 + 随机 CDP 口基线）：裸 spawn 固定口
 * 9335 有残留抢占互驱风险，且 mousePressed 派发在 mobile 模拟下不触发 onclick——
 * 三组窗口/DPR 对照实测，触摸合成 click 全链路稳定，clickAt 已改触摸。
 * 用法：先起临时服务，再 SERVICE=http://127.0.0.1:<port> node tests/ui_mobile.mjs */
import { bootEdge } from "./_ui_boot.mjs";

const SERVICE = process.env.SERVICE || "http://127.0.0.1:18933";

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 240)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // 窗口起桌面尺寸（基线），进页面后 override 成手机视口——clear 时可验证桌面恢复
  const { ws, send, evalJs, envInfo, close } = await bootEdge({ width: 1440, height: 950 });
  console.log("  · " + envInfo.line);

  try {
    check("Edge headless 启动并开放 CDP", !!ws);
    const clickAt = async (sel) => {
      const box = JSON.parse(await evalJs(`(() => {
        const el = document.querySelector(${JSON.stringify(sel)});
        if (!el) return "null";
        // 坐标取按钮本体：SVG <use> 的 getBoundingClientRect 在 mobile 模拟下
        // 可能返回 shadow 盒而非视口坐标（实案：点 use 坐标落空），触摸语义靠合成 click 补齐。
        // scrollIntoView 让长导航里视口外的项（如 __phone）滚进抽屉可视区——
        // 此前冤枉过它：rail 点击落空真因是抽屉打开时全屏遮罩盖住了 rail
        const btn = el.closest("button") || el;
        btn.scrollIntoView({ block: "center", inline: "center" });
        const r = btn.getBoundingClientRect();
        return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
      })()`));
      if (!box || box === "null") return false;
      await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: box.x, y: box.y }] });
      await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      return true;
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
      const use = document.querySelector('.rail-btn[data-rail-page="tasks"] use');
      return use && use.closest("button") ? use.closest("button").dataset.railPage : "none";
    })()`);
    check("从 svg <use> 能 closest 到按钮（抽屉收起依赖此行为）", closest === "tasks", String(closest));

    // 真实点导航轨设置图标（side-foot 已随导航轨改造 b1f1116 隐藏，
    // 设置入口=rail 代理按钮 onclick 转发 btn-settings.click()）→ 进设置页且抽屉收着。
    // 注意 rail 点按必须在抽屉收起时做：抽屉打开时全屏遮罩（标准抽屉语义）盖住 rail，
    // 点击只会收抽屉——这与 ui_mobile_flow.mjs 的通过路径一致。
    await evalJs(`document.body.classList.add("side-collapsed"); "ok"`);
    await sleep(400);
    await clickAt('.rail-btn[title="设置"] use');
    await sleep(700);
    const afterGear = JSON.parse(await evalJs(`JSON.stringify({
      collapsed: document.body.classList.contains("side-collapsed"),
      settingsMode: document.body.classList.contains("settings-mode"),
      menuHidden: document.getElementById("ctx-menu").classList.contains("hidden")
    })`));
    check("点导航轨设置图标即进设置页且抽屉自动收起",
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
      afterNav.collapsed && afterNav.title === "模型调度（可选）", JSON.stringify(afterNav));

    // 手机连接入口：side-foot 的 #btn-phone-side 已随 b1f1116 隐藏，
    // 现走设置导航「手机连接」项（弹框、不切子页）
    await evalJs(`document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(400);
    await clickAt('.set-item[data-sub="__phone"] use');
    await sleep(600);
    const viaNav = JSON.parse(await evalJs(`JSON.stringify({
      collapsed: document.body.classList.contains("side-collapsed"),
      modal: !document.getElementById("modal").classList.contains("hidden"),
      title: document.getElementById("modal-title").textContent,
      settingsMode: document.body.classList.contains("settings-mode")
    })`));
    check("设置导航「手机连接」弹扫码框且抽屉收起（不切子页）",
      viaNav.collapsed && viaNav.modal && /手机连接/.test(viaNav.title) && viaNav.settingsMode, JSON.stringify(viaNav));

    ws.close();
  } finally {
    await close();
  }

  const bad = results.filter((x) => !x).length;
  console.log("\n===== 手机抽屉/图标点击：%d 通过 / %d 失败 =====", results.length - bad, bad);
  if (bad) process.exit(1);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
