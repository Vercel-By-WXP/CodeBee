/* 大屏入口验证：顶栏新增「任务大屏」按钮（i-gauge）→ 新标签页打开 /board.html。
 * 覆盖：按钮存在/位置（不得破坏 btn-theme→btn-insp→conn 链）/图标/不塌陷 →
 * 点击走 openBoard()（stub window.open 捕获 URL）→ 英文态 title 翻译 →
 * board.html 页面真实可达。端口 18921 / CDP 9359（防并行撞车）。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18921;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9359);
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

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uiboard-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "settings.json"), JSON.stringify({ cleanup_enabled: false }), "utf8");

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
    check("board.html 可达", (await fetch(SERVICE + "/board.html")).status === 200);

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
    const waitFor = async (expr, ms = 15000) => {
      for (let t = 0; t < ms; t += 400) {
        if (await evalJs(expr)) return true;
        await sleep(400);
      }
      return false;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll && poll(); "ok"`);
    check("主界面加载", await waitFor(`typeof S !== "undefined" && !!document.getElementById("btn-board")`));

    // 1) 按钮形态：位置（lang-wrap 之前，不动 insp 链）+ 图标 + 不塌陷
    const shape = JSON.parse(await evalJs(`(() => {
      const b = document.getElementById("btn-board");
      const ta = document.querySelector(".top-actions");
      const r = b.getBoundingClientRect();
      return JSON.stringify({
        inActions: ta.contains(b),
        beforeLang: !!(b.nextElementSibling && b.nextElementSibling.classList.contains("lang-wrap")),
        inspChainOk: (() => { const i = document.getElementById("btn-insp"); const c = i && i.nextElementSibling;
          return i && i.previousElementSibling.id === "btn-theme" && c && c.id === "conn"; })(),
        icon: (b.querySelector("svg.ico use") || {}).getAttribute ? b.querySelector("svg.ico use").getAttribute("href") : null,
        w: Math.round(r.width), h: Math.round(r.height),
      });
    })()`));
    check("按钮在顶栏动作组内", shape.inActions, JSON.stringify(shape));
    check("位于语言按钮之前（不破坏 btn-theme→btn-insp→conn 链）",
      shape.beforeLang && shape.inspChainOk, JSON.stringify(shape));
    check("图标为仪表盘 i-gauge", shape.icon === "#i-gauge", String(shape.icon));
    check("纯图标按钮未塌陷成零宽", shape.w >= 26 && shape.h >= 26, `${shape.w}x${shape.h}`);

    // 2) 点击走 openBoard() → stub window.open 捕获 URL（避免真开新页）
    const opened = await evalJs(`(() => {
      let hit = null;
      const real = window.open; window.open = (u, t, f) => { hit = u; return null; };
      try { document.getElementById("btn-board").click(); } finally { window.open = real; }
      return hit;
    })()`);
    check("点击新标签页打开 /board.html", opened === "/board.html", String(opened));

    // 3) 英文态 title/aria 翻译（setLang 只切标记，重译由 applyI18n 完成——与 pickLang 路径一致）
    await evalJs(`setLang("en"); applyI18n(); "ok"`);
    await sleep(300);
    const en = JSON.parse(await evalJs(`(() => {
      const b = document.getElementById("btn-board");
      return JSON.stringify({ title: b.title, aria: b.getAttribute("aria-label") });
    })()`));
    check("英文态提示语翻译为 Mission board",
      en.title === "Mission board" && en.aria === "Mission board", JSON.stringify(en));

    // 4) board 页真实打开后令牌门与主界面共用 orch.token：种 token 后导航不再弹令牌门
    await evalJs(`localStorage.setItem("orch.token", "t-xxxx"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/board.html" });
    await sleep(1500);
    const gateFree = await evalJs(
      `document.getElementById("gate") ? document.getElementById("gate").classList.contains("hidden") : true`);
    check("board 页复用主界面令牌（本机免输令牌门）", gateFree === true, String(gateFree));

    // 5) board.js 本地 t() 必须透传占位符参数——单参封装曾把 {0}/{1} 原样吐在卡片上
    const pct = await evalJs(`window.t ? t("预估 {0} · 进度≈{1}%", "40分", 55) : "(no t)"`);
    check("board 本地 t() 透传占位符（预估/进度不再显示 {0}{1}）",
      typeof pct === "string" && !pct.includes("{0}") && !pct.includes("{1}")
      && pct.includes("40分") && pct.includes("55"), String(pct));
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
  console.log("\n===== 大屏入口 UI：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
