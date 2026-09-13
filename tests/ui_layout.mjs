/* 侧栏几何核验：左下角图标入口的位置/对齐，以及设置导航「图标列—文字列」是否严格对齐。
 * 对齐是「看上去统一」的可量化判据：所有图标左边缘同 x、所有文字左边缘同 x。
 * 用法：先起临时服务（tests/ui_check.mjs 顶部的 SERVICE），再 node tests/ui_layout.mjs */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const CDP_PORT = 9336;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-lay-"));
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
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- 1) 左下角：两个图标按钮同尺寸、贴底、左右分布 ---- */
    const foot = JSON.parse(await evalJs(`(() => {
      const side = document.getElementById("sidebar").getBoundingClientRect();
      const f = document.querySelector(".side-foot").getBoundingClientRect();
      const g = document.getElementById("btn-settings").getBoundingClientRect();
      const p = document.getElementById("btn-phone-side").getBoundingClientRect();
      return JSON.stringify({
        sameSize: Math.abs(g.width - p.width) < 0.5 && Math.abs(g.height - p.height) < 0.5,
        size: [g.width, g.height],
        gapFromSideLeft: g.left - side.left,
        phoneGapToRight: side.right - p.right,
        footBottomGap: side.bottom - f.bottom,
        verticallyAligned: Math.abs(g.top - p.top) < 0.5,
        settingsLeftOfPhone: g.right <= p.left,
      });
    })()`));
    check("左下角两个图标按钮同尺寸", foot.sameSize, JSON.stringify(foot));
    check("两者垂直居中对齐", foot.verticallyAligned, JSON.stringify(foot));
    check("设置靠左 / 手机连接靠右，互不重叠", foot.settingsLeftOfPhone, JSON.stringify(foot));
    check("图标按钮贴底且左右留白对称",
      foot.footBottomGap >= 0 && Math.abs(foot.gapFromSideLeft - foot.phoneGapToRight) < 8,
      JSON.stringify(foot));

    /* ---- 2) 设置导航：图标左边缘同 x、文字左边缘同 x（统一风格的核心） ---- */
    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(900);
    const nav = JSON.parse(await evalJs(`(() => {
      const rows = [...document.querySelectorAll(".side-settings .set-item")].map(b => {
        const svg = b.querySelector("svg.ico").getBoundingClientRect();
        // 用 Range 量纯文字起点（textContent 含图标，不能直接量按钮）
        const tn = [...b.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
        const rg = document.createRange();
        rg.selectNodeContents(tn);
        const t = rg.getBoundingClientRect();
        return { sub: b.dataset.sub, iconL: +svg.left.toFixed(1), iconW: +svg.width.toFixed(1), textL: +t.left.toFixed(1), textW: +t.width.toFixed(1) };
      });
      const back = document.querySelector("#btn-set-back");
      const bsvg = back.querySelector("svg.ico").getBoundingClientRect();
      const btn = [...back.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
      const brg = document.createRange(); brg.selectNodeContents(btn);
      const bt = brg.getBoundingClientRect();
      return JSON.stringify({ rows, back: { iconL: +bsvg.left.toFixed(1), textL: +bt.left.toFixed(1) } });
    })()`));
    const iconXs = [...new Set(nav.rows.map((r) => r.iconL))];
    const textXs = [...new Set(nav.rows.map((r) => r.textL))];
    check("导航图标左边缘严格同 x（图标列对齐）", iconXs.length === 1, JSON.stringify(iconXs));
    check("导航文字左边缘严格同 x（文字列对齐）", textXs.length === 1, JSON.stringify(textXs));
    check("图标与文字间距一致且不重叠",
      nav.rows.every((r) => r.textL - (r.iconL + r.iconW) > 4) && nav.rows.every((r) => r.iconW >= 15),
      JSON.stringify(nav.rows.slice(0, 2)));
    check("「返回 Tutti」与导航项同一图标列/文字列",
      Math.abs(nav.back.iconL - nav.rows[0].iconL) < 0.5 && Math.abs(nav.back.textL - nav.rows[0].textL) < 0.5,
      JSON.stringify(nav.back));

    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r3-settings-nav.png"), Buffer.from(shot.result.data, "base64"));

    /* ---- 3) 选中态：强调色图标，且不因加粗导致文字位移（避免点击时抖动） ---- */
    const before = nav.rows.find((r) => r.sub === "tasks");
    await evalJs(`switchTab("bindings"); "ok"`);
    await sleep(700);
    const act = JSON.parse(await evalJs(`(() => {
      const b = document.querySelector('.set-item[data-sub="bindings"]');
      const tn = [...b.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
      const rg = document.createRange(); rg.selectNodeContents(tn);
      const t = rg.getBoundingClientRect();
      return JSON.stringify({
        active: b.classList.contains("active"),
        iconColor: getComputedStyle(b.querySelector("svg.ico")).color,
        textL: +t.left.toFixed(1), weight: getComputedStyle(b).fontWeight
      });
    })()`));
    check("选中项加粗但不发生文字位移（对齐稳定）",
      act.active && Math.abs(act.textL - before.textL) < 0.5, JSON.stringify({ act, before: before.textL }));
    check("选中项图标用强调色", act.iconColor !== "rgb(166, 166, 166)", act.iconColor);

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((x) => !x).length;
  console.log("\n===== 侧栏几何核验：%d 通过 / %d 失败 =====", results.length - bad, bad);
  if (bad) process.exit(1);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
