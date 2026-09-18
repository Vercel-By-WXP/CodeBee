/* 图标渲染核验：Edge headless + CDP。
 * 不只断言 DOM 里有 <use>，而是量每个图标的实际包围盒、几何非空、以及
 * 截图里对应区域确实有「墨迹」（像素方差 > 0），避免出现空白方块/占位符。
 * 用法：先起临时服务（见 tests/ui_check.mjs 顶部 SERVICE），再 node tests/ui_icons.mjs */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const CDP_PORT = 9334;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-ico-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
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
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- 1) 精灵表本身：每个 symbol 几何非空（getBBox 有实际宽高） ---- */
    const sprite = await evalJs(`(() => {
      const out = [];
      document.querySelectorAll("#icon-sprite symbol").forEach(s => {
        const bb = s.getBBox();
        out.push({ id: s.id, w: +bb.width.toFixed(2), h: +bb.height.toFixed(2), kids: s.children.length });
      });
      return JSON.stringify(out);
    })()`);
    const syms = JSON.parse(sprite);
    check("精灵表含全部图标 symbol", syms.length >= 13, "实际 " + syms.length);
    const empty = syms.filter((s) => !(s.w > 4 && s.h > 4 && s.kids > 0));
    check("每个 symbol 几何非空（有实际图形）", empty.length === 0, JSON.stringify(empty));

    /* ---- 2) 侧栏左下角图标：实际尺寸 + 描边色随文字色 + 非 emoji ---- */
    const foot = await evalJs(`(() => {
      const out = {};
      for (const id of ["btn-settings", "btn-phone-side"]) {
        const b = document.getElementById(id);
        const svg = b.querySelector("svg.ico");
        const r = svg.getBoundingClientRect();
        const cs = getComputedStyle(svg);
        out[id] = {
          w: +r.width.toFixed(1), h: +r.height.toFixed(1),
          stroke: cs.stroke, fill: cs.fill, color: getComputedStyle(b).color,
          text: b.textContent.trim(), use: svg.querySelector("use").getAttribute("href"),
        };
      }
      return JSON.stringify(out);
    })()`);
    const f = JSON.parse(foot);
    for (const id of ["btn-settings", "btn-phone-side"]) {
      const v = f[id];
      check(id + " 图标按 19px 渲染且不塌陷", v.w >= 17 && v.h >= 17, JSON.stringify(v));
      check(id + " 描边用 currentColor（跟随主题）", v.stroke === v.color && v.fill === "none", JSON.stringify(v));
      check(id + " 无文字残留（纯图标按钮）", v.text === "", JSON.stringify(v));
      check(id + " use 指向已定义 symbol", ["#i-gear", "#i-phone"].includes(v.use), v.use);
    }

    /* ---- 3) 截图取像素：图标区域必须有「墨迹」，不是空白/纯色块 ---- */
    await evalJs(`exitSettings(); localStorage.removeItem("orch.setTab"); "ok"`);
    await sleep(500);
    const boxes = await evalJs(`(() => {
      const pick = (sel) => { const r = document.querySelector(sel).getBoundingClientRect();
        return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      return JSON.stringify({
        gear: pick("#btn-settings"), phone: pick("#btn-phone-side"),
        menu: pick("#btn-menu"), theme: pick("#btn-theme"),
      });
    })()`);
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const png = Buffer.from(shot.result.data, "base64");
    writeFileSync(join(ROOT, ".ui-shots", "r3-icons.png"), png);

    // 在页面里用 canvas 解码截图并统计每个图标框内的灰度方差（方差≈0 即空白）
    const ink = await evalJs(`(async () => {
      const boxes = ${boxes};
      const b64 = ${JSON.stringify(png.toString("base64"))};
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = "data:image/png;base64," + b64; });
      const c = document.createElement("canvas");
      c.width = img.width; c.height = img.height;
      const g = c.getContext("2d");
      g.drawImage(img, 0, 0);
      const out = {};
      for (const [k, b] of Object.entries(boxes)) {
        const d = g.getImageData(b.x, b.y, Math.max(1, b.w), Math.max(1, b.h)).data;
        const lum = [];
        for (let i = 0; i < d.length; i += 4) lum.push(0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2]);
        const mean = lum.reduce((a, v) => a + v, 0) / lum.length;
        const sd = Math.sqrt(lum.reduce((a, v) => a + (v - mean) ** 2, 0) / lum.length);
        out[k] = { w: b.w, h: b.h, sd: +sd.toFixed(2), min: Math.round(Math.min(...lum)), max: Math.round(Math.max(...lum)) };
      }
      return JSON.stringify(out);
    })()`);
    const inkData = JSON.parse(ink);
    // theme 现在是纯图标按钮，同样必须真的有墨迹（否则就是一个空胶囊）
    for (const k of ["gear", "phone", "menu", "theme"]) {
      const v = inkData[k];
      check("截图里 " + k + " 图标区域有实际墨迹（非空白块）", v.sd > 3, JSON.stringify(v));
    }

    /* ---- 4) 设置导航：8 项图标尺寸一致（风格统一的可量化判据） ---- */
    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(900);
    const nav = await evalJs(`(() => {
      const rows = [...document.querySelectorAll(".side-settings .set-item")].map(b => {
        const svg = b.querySelector("svg.ico"), r = svg.getBoundingClientRect();
        return { sub: b.dataset.sub, w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                 stroke: getComputedStyle(svg).strokeWidth, href: svg.querySelector("use").getAttribute("href"),
                 text: b.textContent.trim() };
      });
      const back = document.querySelector("#btn-set-back svg.ico").getBoundingClientRect();
      return JSON.stringify({ rows, back: { w: +back.width.toFixed(1), h: +back.height.toFixed(1) } });
    })()`);
    const nv = JSON.parse(nav);
    const sizes = new Set(nv.rows.map((r) => r.w + "x" + r.h));
    check("设置导航 8 项图标尺寸完全一致", nv.rows.length >= 8 && sizes.size === 1, JSON.stringify([...sizes]));
    const strokes = new Set(nv.rows.map((r) => r.stroke));
    check("设置导航图标线宽一致", strokes.size === 1, JSON.stringify([...strokes]));
    check("返回按钮图标与导航项同尺寸", nv.rows[0].w === nv.back.w && nv.rows[0].h === nv.back.h, JSON.stringify(nv.back));
    const hrefs = nv.rows.map((r) => r.href);
    check("每项用各自的语义图标（无重复占位）", new Set(hrefs).size === hrefs.length, JSON.stringify(hrefs));
    const emojiLeft = nv.rows.filter((r) => /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{2BFF}]/u.test(r.text));
    check("导航标签为纯文字（emoji 已全部替换）", emojiLeft.length === 0, JSON.stringify(emojiLeft.map((r) => r.text)));

    const shot2 = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r3-settings-nav.png"), Buffer.from(shot2.result.data, "base64"));

    /* ---- 4b) 顶栏精简：模型胶囊/问号已移除，明暗按钮为纯图标 ---- */
    const topbar = JSON.parse(await evalJs(`(() => {
      const ta = document.querySelector(".top-actions");
      const th = document.getElementById("btn-theme");
      const r = th.getBoundingClientRect();
      return JSON.stringify({
        hasModelPill: !!document.getElementById("model-pill"),
        hasModelText: !!document.getElementById("model-pill-text"),
        hasQMark: !!document.querySelector(".q-mark"),
        themeText: th.textContent.trim(),
        themeIcon: (th.querySelector("svg.ico use") || {}).getAttribute
          ? th.querySelector("svg.ico use").getAttribute("href") : null,
        themeW: Math.round(r.width),
        themeH: Math.round(r.height),
        actions: (ta.textContent || "").replace(/\\s+/g, " ").trim(),
        overflow: ta.scrollWidth - ta.clientWidth
      });
    })()`));
    check("顶栏已移除模型胶囊（含其文字节点）",
      !topbar.hasModelPill && !topbar.hasModelText, JSON.stringify(topbar));
    check("顶栏已移除问号帮助符号", !topbar.hasQMark, JSON.stringify(topbar));
    check("顶栏明暗按钮为纯图标且保留了图标",
      topbar.themeText === "" && /^#i-(moon|sun)$/.test(topbar.themeIcon || ""),
      JSON.stringify(topbar));
    check("纯图标按钮未塌陷成零宽", topbar.themeW >= 26 && topbar.themeH >= 26, JSON.stringify(topbar));
    check("顶栏无横向溢出，且不再有残留的「模型未绑定」文字",
      topbar.overflow <= 0 && !/模型未绑定/.test(topbar.actions), JSON.stringify(topbar));

    /* ---- 5) 主题切换：图标=当前主题（默认日间 ocean 亮色→太阳），点击切夜间→月亮，再点回日间 ---- */
    const themeLight = await evalJs(`document.querySelector("#btn-theme svg.ico use").getAttribute("href")`);
    check("默认日间主题显示太阳图标", themeLight === "#i-sun", themeLight);
    await evalJs(`document.getElementById("btn-theme").click(); "ok"`);
    await sleep(500);
    const dark = await evalJs(`(() => {
      const svg = document.querySelector("#btn-theme svg.ico");
      return JSON.stringify({
        theme: document.documentElement.dataset.theme,
        href: svg.querySelector("use").getAttribute("href"),
        stroke: getComputedStyle(svg).stroke,
        color: getComputedStyle(document.getElementById("btn-theme")).color,
      });
    })()`);
    const dk = JSON.parse(dark);
    check("切夜间后换成月亮图标", dk.theme === "dark" && dk.href === "#i-moon", JSON.stringify(dk));
    check("夜间下图标描边仍跟随文字色", dk.stroke === dk.color, JSON.stringify(dk));
    await evalJs(`document.getElementById("btn-theme").click(); "ok"`);   // 还原日间
    await sleep(300);
    const back = await evalJs(`(() => ({
      theme: document.documentElement.dataset.theme,
      href: document.querySelector("#btn-theme svg.ico use").getAttribute("href"),
    }))()`);
    check("再切回日间恢复太阳图标", back.theme === "light" && back.href === "#i-sun", JSON.stringify(back));
    const shot3 = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r3-settings-nav-light.png"), Buffer.from(shot3.result.data, "base64"));

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 图标核验：%d 通过 / %d 失败 =====", results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
