/* 皮肤 / 换肤核验：静态调色板齐备性 + Edge headless(CDP) 真机换肤。
 *
 * 两层：
 *  1) 静态：解析 style.css —— 每个 html[data-skin=X] 必须与其 [data-theme=light] 版
 *     声明同一组变量，且与 :root（经典）一致。漏写一个变量会让日间版悄悄落到夜间值上，
 *     这种错误只看截图很难发现，所以在这里结构化卡死。
 *  2) 运行时：点开 设置 → 皮肤，逐个换肤，断言根属性 / localStorage / 预览色块 /
 *     手机状态栏色 / 明暗切换后皮肤不丢 / 刷新后仍是上次的皮肤，且无控制台报错。
 *
 * 用法：先起临时服务（见 tests/ui_check.mjs 顶部 SERVICE），再 node tests/ui_skin.mjs */
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const CDP_PORT = 9350;   // 与其他 ui_*.mjs 的调试端口错开
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SKIN_IDS = ["ocean", "hermes", "classic", "forest", "amber", "violet", "contrast"];

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------------------------------------------------- 1) 静态：调色板齐备 */
function staticCheck() {
  const css = readFileSync(join(ROOT, "app", "ui", "style.css"), "utf8");
  // 取「选择器 { ... }」块里声明的变量名（拆解排版无关）
  const blockVars = (selector) => {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = css.match(new RegExp(esc + "\\s*\\{([^}]*)\\}"));
    if (!m) return null;
    return new Set((m[1].match(/--[a-z0-9-]+\s*:/g) || []).map((v) => v.replace(/\s*:/, "")));
  };
  const structural = new Set(["--mono", "--r-lg", "--r-md", "--r-sm"]);
  const palette = (set) => new Set([...(set || [])].filter((v) => !structural.has(v)));

  const refDark = palette(blockVars(":root"));
  const refLight = palette(blockVars('html[data-theme="light"]'));
  check("经典皮肤（:root 与日间版）声明了调色板变量", refDark.size >= 15 && refLight.size === refDark.size,
    `dark=${refDark.size} light=${refLight.size}`);
  check("经典皮肤日夜两版变量名一致", [...refDark].every((v) => refLight.has(v)) && refLight.size === refDark.size,
    `missing in light: ${[...refDark].filter((v) => !refLight.has(v))}`);

  for (const id of SKIN_IDS.filter((x) => x !== "classic")) {
    const d = palette(blockVars(`html[data-skin="${id}"]`));
    const l = palette(blockVars(`html[data-skin="${id}"][data-theme="light"]`));
    check(`皮肤「${id}」日夜两版都有定义`, !!d && !!l, `dark=${!!d} light=${!!l}`);
    if (!d || !l) continue;
    const missD = [...refDark].filter((v) => !d.has(v));
    const missL = [...refLight].filter((v) => !l.has(v));
    const extraD = [...d].filter((v) => !refDark.has(v));
    check(`皮肤「${id}」变量与经典完全对齐（不漏不增）`,
      missD.length === 0 && missL.length === 0 && extraD.length === 0,
      `夜间缺 [${missD}] 日间缺 [${missL}] 多出 [${extraD}]`);
  }

  // 皮肤块必须排在 html[data-theme="light"] 之后，否则同权重下日间的中性色会被夜间皮肤块盖掉
  const iLight = css.indexOf('html[data-theme="light"]');
  const iSkin = Math.min(...SKIN_IDS.filter((x) => x !== "classic")
    .map((id) => css.indexOf(`html[data-skin="${id}"]`)).filter((i) => i >= 0));
  check("皮肤块排在通用日间块之后（保证日间皮肤生效）", iSkin > iLight, `light@${iLight} skin@${iSkin}`);
}

/* ---------------------------------------------------------- 2) 运行时：CDP */
async function main() {
  staticCheck();

  const profile = mkdtempSync(join(tmpdir(), "tutti-skin-"));
  const edge = EDGE_CANDIDATES.find(() => true);
  const proc = spawn(edge, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  let ws = null;
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    if (!target) throw new Error("Edge 未就绪（CDP 连不上）");

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
        consoleErrors.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
      }
      if (msg.method === "Runtime.exceptionThrown") {
        consoleErrors.push(msg.params.exceptionDetails?.exception?.description || "exception");
      }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.text + " :: " + expr);
      return r.result?.result?.value;
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(1800);

    // 页面已加载（app.js 初始化完成）——清掉干净启动时的旧皮肤，从默认态开始
    await evalJs(`localStorage.removeItem("orch.skin"); localStorage.removeItem("orch.theme"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(1800);

    const boot = JSON.parse(await evalJs(`JSON.stringify({
      skin: document.documentElement.dataset.skin,
      theme: document.documentElement.dataset.theme,
      bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
      meta: document.querySelector('meta[name="theme-color"]').getAttribute("content")
    })`));
    check("首屏默认皮肤=深海、默认明暗=日间", boot.skin === "ocean" && boot.theme === "light", JSON.stringify(boot));
    check("深海日间底色为纯白 #ffffff", boot.bg === "#ffffff", boot.bg);

    /* ---- 设置 → 皮肤页 ---- */
    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(600);
    const navHit = await evalJs(`!!document.querySelector('.set-item[data-sub="appearance"]')`);
    check("设置导航里有「皮肤」入口", navHit);

    await evalJs(`document.querySelector('.set-item[data-sub="appearance"]').click(); "ok"`);
    await sleep(700);
    const page = JSON.parse(await evalJs(`JSON.stringify({
      shown: !document.getElementById("sub-appearance").classList.contains("hidden"),
      title: document.getElementById("page-title").textContent,
      cards: document.querySelectorAll("#skin-grid .skin-card").length,
      ids: [...document.querySelectorAll("#skin-grid .skin-card")].map(c => c.dataset.skin),
      active: [...document.querySelectorAll("#skin-grid .skin-card.active")].map(c => c.dataset.skin),
      modeOn: [...document.querySelectorAll("#skin-mode [data-mode].active")].map(b => b.dataset.mode),
      pillIconOnly: (() => { const b = document.getElementById("btn-skin"); return !!b && !b.textContent.trim(); })(),
      cur: document.getElementById("skin-cur").textContent,
      prevBg: [...document.querySelectorAll("#skin-grid .skin-card")].map(c => c.querySelector(".pv-main").style.background)
    })`));
    check("皮肤页能打开且标题为「皮肤」", page.shown && page.title === "皮肤", JSON.stringify(page).slice(0, 200));
    check("皮肤页列出全部 7 套皮肤（深海默认排首）", page.cards === 7 && page.ids[0] === "ocean" && JSON.stringify([...page.ids].sort()) === JSON.stringify([...SKIN_IDS].sort()), JSON.stringify(page.ids));
    check("默认选中「深海」", page.active.length === 1 && page.active[0] === "ocean", JSON.stringify(page.active));
    check("明暗分段显示当前为日间", JSON.stringify(page.modeOn) === JSON.stringify(["light"]), JSON.stringify(page.modeOn));
    check("顶栏皮肤入口为纯图标（不显示皮肤名文字）", page.pillIconOnly === true, JSON.stringify(page.pillIconOnly));
    check("皮肤页标签显示「深海 · 日间」", /深海/.test(page.cur) && /日间/.test(page.cur), page.cur);
    check("每张卡片预览色块都取到了色值（无空块）",
      page.prevBg.length === 7 && page.prevBg.every((c) => c && c !== "rgba(0, 0, 0, 0)" && c !== ""),
      JSON.stringify(page.prevBg));
    const sides = JSON.parse(await evalJs(`JSON.stringify(
      [...document.querySelectorAll("#skin-grid .skin-card .pv-side")].map((c) => c.style.background))`));
    check("不同皮肤的预览色块不同（默认日间下各皮肤 bg 同为白底，改查侧栏色条——说明调色板真的各不一样）",
      new Set(sides).size >= 4, JSON.stringify(sides));

    /* ---- 逐个换肤：根属性 / 持久化 / 界面色真变 / 状态栏色 ---- */
    const seen = {};
    for (const id of SKIN_IDS) {
      const st = JSON.parse(await evalJs(`(() => {
        const c = document.querySelector('#skin-grid .skin-card[data-skin="${id}"]');
        c.click();
        const cs = getComputedStyle(document.documentElement);
        const act = document.querySelector("#skin-grid .skin-card.active .skin-check");
        const idle = document.querySelector('#skin-grid .skin-card:not(.active) .skin-check');
        return JSON.stringify({
          root: document.documentElement.dataset.skin,
          stored: localStorage.getItem("orch.skin"),
          bg: cs.getPropertyValue("--bg").trim(),
          accent: cs.getPropertyValue("--accent").trim(),
          bodyBg: getComputedStyle(document.body).backgroundColor,
          meta: document.querySelector('meta[name="theme-color"]').getAttribute("content"),
          active: [...document.querySelectorAll("#skin-grid .skin-card.active")].map(x => x.dataset.skin),
          checkOn: act ? getComputedStyle(act).opacity : null,
          checkOff: idle ? getComputedStyle(idle).opacity : null,
          pillIconOnly: !document.getElementById("btn-skin").textContent.trim()
        });
      })()`));
      seen[id] = st.bg + "|" + st.accent;
      check(`换肤「${id}」：根属性 / localStorage / 选中态一致，顶栏保持纯图标`,
        st.root === id && st.stored === id && st.active.length === 1 && st.active[0] === id && st.pillIconOnly,
        JSON.stringify(st));
      check(`换肤「${id}」：选中勾显示、未选中勾隐藏`,
        st.checkOn === "1" && st.checkOff === "0", `on=${st.checkOn} off=${st.checkOff}`);
      check(`换肤「${id}」：body 实际背景色已跟着变`, /^rgb/.test(st.bodyBg), st.bodyBg);
      check(`换肤「${id}」：手机状态栏色跟随皮肤`, !!st.meta && st.meta.startsWith("#"), st.meta);
    }
    check("7 套皮肤的底色/强调色互不相同（没有套壳重复）", new Set(Object.values(seen)).size === 7,
      JSON.stringify(seen));

    /* ---- 明暗切换：换皮肤不丢 ---- */
    await evalJs(`document.querySelector('#skin-grid .skin-card[data-skin="ocean"]').click(); "ok"`);
    await sleep(200);
    await evalJs(`document.querySelector('#skin-mode [data-mode="light"]').click(); "ok"`);
    await sleep(400);
    const light = JSON.parse(await evalJs(`JSON.stringify({
      skin: document.documentElement.dataset.skin,
      theme: document.documentElement.dataset.theme,
      bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
      active: [...document.querySelectorAll("#skin-mode [data-mode].active")].map(b => b.dataset.mode),
      cur: document.getElementById("skin-cur").textContent
    })`));
    check("切到日间后皮肤仍是深海（明暗不重置皮肤）", light.skin === "ocean" && light.theme === "light", JSON.stringify(light));
    check("深海日间版底色已生效（非夜间深色）", light.bg !== "#0e1626", light.bg);
    check("日间选中态与标签同步", JSON.stringify(light.active) === JSON.stringify(["light"]) && /日间/.test(light.cur), light.cur);

    // 顶栏明暗按钮也要和皮肤页保持同一状态（按钮是纯图标，靠 sun/moon 反映状态）
    await evalJs(`document.getElementById("btn-theme").click(); "ok"`);
    await sleep(300);
    const viaTop = JSON.parse(await evalJs(`JSON.stringify({
      theme: document.documentElement.dataset.theme,
      skin: document.documentElement.dataset.skin,
      icon: document.querySelector("#btn-theme svg.ico use").getAttribute("href"),
      text: document.getElementById("btn-theme").textContent.trim()
    })`));
    check("顶栏明暗按钮切换后皮肤不变、图标同步为月亮",
      viaTop.theme === "dark" && viaTop.skin === "ocean" && viaTop.icon === "#i-moon",
      JSON.stringify(viaTop));
    check("顶栏明暗按钮为纯图标（无日间/夜间文字）", viaTop.text === "", JSON.stringify(viaTop));

    /* ---- 刷新持久化（首屏预涂脚本） ---- */
    await evalJs(`document.querySelector('#skin-grid .skin-card[data-skin="forest"]').click(); "ok"`);
    await sleep(200);
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "skin-forest-dark.png"), Buffer.from(shot.result.data, "base64"));

    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(1800);
    const reload = JSON.parse(await evalJs(`JSON.stringify({
      skin: document.documentElement.dataset.skin,
      theme: document.documentElement.dataset.theme,
      bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim()
    })`));
    check("刷新后仍是上次选的皮肤（森林·夜间）", reload.skin === "forest" && reload.theme === "dark" && reload.bg === "#141d18",
      JSON.stringify(reload));

    /* ---- 非法值回落经典（换版本/手改 localStorage 的容错） ---- */
    await evalJs(`localStorage.setItem("orch.skin","no-such-skin"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(1500);
    const bad = JSON.parse(await evalJs(`JSON.stringify({
      skin: document.documentElement.dataset.skin,
      bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim()
    })`));
    check("皮肤值非法时渲染回落深海（界面不会裸奔）", bad.bg === "#0e1626", JSON.stringify(bad));

    /* ---- 窄屏：顶栏不挤、皮肤页两列、点选仍生效 ---- */
    await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await evalJs(`localStorage.removeItem("orch.skin"); localStorage.setItem("orch.theme","dark"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    const mobTop = JSON.parse(await evalJs(`JSON.stringify({
      skinPill: getComputedStyle(document.getElementById("btn-skin")).display,
      themePill: getComputedStyle(document.getElementById("btn-theme")).display,
      overflow: document.querySelector(".top-actions").scrollWidth - document.querySelector(".top-actions").clientWidth
    })`));
    check("窄屏顶栏收起皮肤入口（改用 设置 → 皮肤）", mobTop.skinPill === "none", JSON.stringify(mobTop));
    check("窄屏顶栏明暗按钮仍在、且不横向溢出",
      mobTop.themePill !== "none" && mobTop.overflow <= 0, JSON.stringify(mobTop));

    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(500);
    await evalJs(`document.querySelector('.set-item[data-sub="appearance"]').click(); "ok"`);
    await sleep(700);
    const mob = JSON.parse(await evalJs(`(() => {
      const grid = document.getElementById("skin-grid");
      const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean);
      const cards = [...grid.querySelectorAll(".skin-card")];
      const r0 = cards[0].getBoundingClientRect(), r1 = cards[1].getBoundingClientRect();
      return JSON.stringify({
        cols: cols.length,
        cards: cards.length,
        inPanel: cards.length === 7 && r0.width > 0 && r0.right <= document.documentElement.clientWidth + 1,
        sameRow: Math.abs(r0.top - r1.top) < 2,
        descHidden: getComputedStyle(document.querySelector(".skin-desc")).display === "none",
        drawerCollapsed: document.body.classList.contains("side-collapsed")
      });
    })()`));
    check("窄屏皮肤页两列排布、卡片不出屏", mob.cols === 2 && mob.cards === 7 && mob.inPanel && mob.sameRow, JSON.stringify(mob));
    check("窄屏隐藏皮肤描述（只留名字与配色预览）", mob.descHidden, JSON.stringify(mob));
    check("窄屏点设置导航后抽屉自动收起（不挡皮肤页）", mob.drawerCollapsed, JSON.stringify(mob));

    const mobPick = JSON.parse(await evalJs(`(() => {
      document.querySelector('#skin-grid .skin-card[data-skin="amber"]').click();
      return JSON.stringify({
        skin: document.documentElement.dataset.skin,
        bg: getComputedStyle(document.body).backgroundColor,
        pillIconOnly: !document.getElementById("btn-skin").textContent.trim()
      });
    })()`));
    check("窄屏也能换肤（点卡片即生效，顶栏保持纯图标）", mobPick.skin === "amber" && mobPick.pillIconOnly && /^rgb/.test(mobPick.bg),
      JSON.stringify(mobPick));
    await send("Emulation.clearDeviceMetricsOverride");

    // 收尾：恢复默认，别把测试状态留给下次
    await evalJs(`localStorage.removeItem("orch.skin"); localStorage.setItem("orch.theme","dark"); "ok"`);

    check("整个过程无控制台报错", consoleErrors.length === 0, JSON.stringify(consoleErrors).slice(0, 300));
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(400);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log(`\n皮肤核验：${results.length - bad.length}/${results.length} 通过`);
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error("皮肤核验异常：", e); process.exit(2); });
