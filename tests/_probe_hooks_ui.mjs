/* 一次性探针：设置 → 钩子管理卡片几何验证（2026-09-22 用户批评样式未验证就发版）。
 * 断言：新建后名称/事件/命令/开关/超时/测试按钮全部可见、不互相重叠、名称框够宽。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18850 + (process.pid % 100);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9860 + (process.pid % 100);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, cond, detail = "") => {
  results.push(cond);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
};

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-hooksui-"));
  const svc = spawn("python", ["app/main.py", "--port", String(PORT), "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) { await sleep(500); try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {} }
  check("临时服务就绪", up);
  const profile = mkdtempSync(join(tmpdir(), "tutti-hooksui-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"],
    { stdio: "ignore" });
  try {
    let wsUrl = null;
    for (let i = 0; i < 30 && !wsUrl; i++) {
      await sleep(400);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json`)).json();
        wsUrl = list.find((t) => t.type === "page")?.webSocketDebuggerUrl;
      } catch (e) {}
    }
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval failed");
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await evalJs(`(function () { if (window.welcomeClose) welcomeClose(); return 1; })()`);
    await evalJs(`switchTab("hooks"); "ok"`);
    await sleep(600);
    check("钩子子页可达", await evalJs(`!document.getElementById("sub-hooks").classList.contains("hidden")`));

    // 新建一条 → 几何断言
    await evalJs(`document.getElementById("btn-hooks-add").click(); "ok"`);
    await sleep(300);
    const g = JSON.parse(await evalJs(`(() => {
      const card = document.querySelector("#hooks-list .hook-card");
      if (!card) return "{}";
      const r = (el) => { const x = el.getBoundingClientRect(); return { l: Math.round(x.left), t: Math.round(x.top), r: Math.round(x.right), b: Math.round(x.bottom), w: Math.round(x.width), h: Math.round(x.height) }; };
      const name = card.querySelector(".hook-name"), ev = card.querySelector(".hook-event"),
            cmd = card.querySelector(".hook-cmd"), nameRect = r(name), evRect = r(ev),
            cmdRect = r(cmd), cardRect = r(card);
      const overlap = (a, b) => !(a.r <= b.l || b.r <= a.l || a.b <= b.t || b.b <= a.t);
      const cs = getComputedStyle(card);
      const dbg = { display: cs.display, fd: cs.flexDirection,
        cls: card.className, html: card.outerHTML.slice(0, 120) };
      return JSON.stringify({dbg,
        nameRect, evRect, cmdRect, footRect: r(card.querySelector(".hook-timeout")),
        cmdPos: getComputedStyle(card.querySelector(".hook-cmd")).position,
        namePos: getComputedStyle(card.querySelector(".hook-name")).position,
        rowRect: r(card.querySelector(".hook-row")),
        rowHTML: card.querySelector(".hook-row").outerHTML.slice(0, 600),
        nameW: nameRect.w, namePlaceholder: name.placeholder,
        evVisible: evRect.w > 80,
        nameEvNoOverlap: !overlap(nameRect, evRect),
        cmdBelowName: cmdRect.t >= nameRect.b,
        cmdInsideCard: cmdRect.l >= cardRect.l - 2 && cmdRect.r <= cardRect.r + 2,
        cmdPlaceholderHasInject: /inject/.test(cmd.placeholder),
        cardH: cardRect.h,
        footVisible: card.querySelector(".hook-timeout").getBoundingClientRect().width > 30
      });
    })()`));
    console.log("FULLG " + JSON.stringify(g));
    check("名称框宽度 ≥160", (g.nameW || 0) >= 160, JSON.stringify(g));
    check("名称 placeholder 为「钩子名称」", g.namePlaceholder === "钩子名称", g.namePlaceholder);
    check("事件下拉可见（>80px）", g.evVisible);
    check("名称与事件不重叠", g.nameEvNoOverlap);
    check("命令行在名称行下方", g.cmdBelowName, JSON.stringify(g));
    check("命令行不越出卡片", g.cmdInsideCard);
    check("命令 placeholder 含协议说明", g.cmdPlaceholderHasInject);
    check("卡片高度合理（≥150px）", (g.cardH || 0) >= 150, JSON.stringify(g.cardH));
    check("超时输入可见", g.footVisible, JSON.stringify(g));

    // 保存链路：填一条命令 → 保存 → 服务端配置落盘
    await evalJs(`(async () => {
      const card = document.querySelector("#hooks-list .hook-card");
      card.querySelector('[data-hf="name"]').value = "测试钩子";
      card.querySelector('[data-hf="cmd"]').value = "python -c \\"print('hi')\\"";
      document.getElementById("btn-hooks-save").click();
      return 1; })()`);
    await sleep(800);
    const saved = await (await fetch(SERVICE + "/api/hooks")).json();
    check("保存后服务端有一条钩子", (saved.hooks || []).length === 1 &&
      saved.hooks[0].name === "测试钩子", JSON.stringify(saved).slice(0, 160));
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const fails = results.filter((x) => !x).length;
  console.log(fails ? `\n${fails}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
