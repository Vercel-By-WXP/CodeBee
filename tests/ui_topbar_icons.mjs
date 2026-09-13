/* 顶栏图标化核验（皮肤 + 控制权胶囊）：Edge headless + CDP。
 * 断言顶栏胶囊全部纯图标（无可见文字 / emoji），控制权三态（空闲 / 我控制 /
 * 他设备控制中）图标正确切换、颜色态 class 正确、语义进 title/aria-label，
 * 且测量图标包围盒非空（不是空白方块）。
 * 用法：先起临时服务（SERVICE 改成对应端口），再 node tests/ui_topbar_icons.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:8803";
const CDP_PORT = 9335;
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
  const profile = mkdtempSync(join(tmpdir(), "tutti-topbar-"));
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

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    // 1) 顶栏四个胶囊/入口全部纯图标：无可见文字、无 emoji、图标包围盒非空
    const bar = JSON.parse(await evalJs(`(() => {
      const box = (el) => { const r = el.querySelector("svg.ico")?.getBoundingClientRect();
                            return r ? { w: +r.width.toFixed(1), h: +r.height.toFixed(1) } : null; };
      const skin = document.getElementById("btn-skin");
      const theme = document.getElementById("btn-theme");
      const ctrl = document.getElementById("ctrl-pill");
      const emoji = /[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u;
      const plain = (el) => !emoji.test(el.textContent);
      return JSON.stringify({
        skin: { text: skin.textContent.trim(), box: box(skin), plain: plain(skin),
                iconOnly: skin.classList.contains("icon-only") },
        theme: { text: theme.textContent.trim(), box: box(theme), plain: plain(theme),
                 iconOnly: theme.classList.contains("icon-only") },
        ctrl: { hidden: ctrl.classList.contains("hidden"), text: ctrl.textContent.trim(),
                plain: plain(ctrl), iconOnly: ctrl.classList.contains("icon-only"),
                box: ctrl.classList.contains("hidden") ? null : box(ctrl),
                use: ctrl.querySelector("use")?.getAttribute("href") }
      });
    })()`));
    check("皮肤胶囊纯图标（无文字 + icon-only 类 + 包围盒非空）",
      bar.skin.text === "" && bar.skin.iconOnly && bar.skin.box && bar.skin.box.w > 8, JSON.stringify(bar.skin));
    check("明暗胶囊纯图标", bar.theme.text === "" && bar.theme.iconOnly && bar.theme.box && bar.theme.box.w > 8,
      JSON.stringify(bar.theme));
    check("顶栏全部入口无 emoji 残留", bar.skin.plain && bar.theme.plain && bar.ctrl.plain, JSON.stringify(bar));

    // 2) 控制权三态：空闲态默认可见（原行为：提示可接管）；注入三态断言图标切换 / 颜色类 / 语义文案
    check("空闲态胶囊可见且为开锁图标", bar.ctrl.hidden === false && bar.ctrl.use === "#i-unlock",
      JSON.stringify(bar.ctrl));

    const states = JSON.parse(await evalJs(`(() => {
      const out = {};
      setControl({ mode: "free" });
      out.free = { use: document.querySelector("#ctrl-pill use").getAttribute("href"),
                   cls: document.getElementById("ctrl-pill").className,
                   tip: document.getElementById("ctrl-pill").title,
                   text: document.getElementById("ctrl-pill").textContent.trim() };
      setControl({ mode: "held", mine: true, holder: "本机" });
      out.mine = { use: document.querySelector("#ctrl-pill use").getAttribute("href"),
                   cls: document.getElementById("ctrl-pill").className,
                   tip: document.getElementById("ctrl-pill").title,
                   text: document.getElementById("ctrl-pill").textContent.trim() };
      setControl({ mode: "held", mine: false, holder: "手机-张三" });
      out.held = { use: document.querySelector("#ctrl-pill use").getAttribute("href"),
                   cls: document.getElementById("ctrl-pill").className,
                   tip: document.getElementById("ctrl-pill").title,
                   text: document.getElementById("ctrl-pill").textContent.trim() };
      return JSON.stringify(out);
    })()`));
    check("空闲态用开锁图标、无文字、语义进 title",
      states.free.use === "#i-unlock" && states.free.text === "" && /控制空闲/.test(states.free.tip),
      states.free);
    check("我控制态用手柄图标 + mine 绿色类",
      states.mine.use === "#i-gamepad" && /mine/.test(states.mine.cls) && states.mine.text === "",
      states.mine);
    check("被占用态用闭锁图标 + held 黄色类 + title 带持有者名",
      states.held.use === "#i-lock" && /held/.test(states.held.cls) && /手机-张三/.test(states.held.tip),
      states.held);

    // 3) 收尾：控制胶囊恢复隐藏，避免污染后续断言
    await evalJs(`setControl(null); "ok"`);

    // 4) 全程无控制台报错由服务兜底：状态接口仍正常
    const state = await fetch(SERVICE + "/api/state").then((r) => r.json());
    check("页面操作期间服务状态正常", Array.isArray(state.agents));

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 顶栏图标化（UI/CDP）：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
