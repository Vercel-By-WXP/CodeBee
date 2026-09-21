/* 一次性探针：composer 工具行 pill（编排/思考/厂商模型菜单）行为与几何验证。
 * 跑法：node tests/_probe_cmp_prefs.mjs（自起临时服务+Edge headless，随机端口） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18900 + (process.pid % 200);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9700 + (process.pid % 200);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, cond, detail = "") => {
  results.push(cond);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
};

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-cmpprefs-"));
  const svc = spawn("python", ["app/main.py", "--port", String(PORT), "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) { await sleep(500); try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {} }
  check("临时服务就绪", up);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cmpprefs-edge-"));
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
    // 临时数据目录=首启：欢迎层模态遮罩会拦截一切真实点击（正常模态行为），
    // 先关掉再测，否则真实坐标点击全被 welcome-mask 吃掉
    await evalJs(`(function () { if (window.welcomeClose) welcomeClose(); return "ok"; })()`);

    // 1) pill 就位与短名
    const st = JSON.parse(await evalJs(`JSON.stringify({
      modeFace: document.getElementById("f-mode-face")?.textContent,
      thinkFace: document.getElementById("f-thinking-face")?.textContent,
      inBar: !!document.querySelector(".cmp-bar .cmp-sel"),
      modeVal: document.getElementById("f-mode").value
    })`));
    check("编排 pill 短名「自动」", st.modeFace === "自动", st.modeFace);
    check("思考 pill 短名「标准」", st.thinkFace === "标准", st.thinkFace);
    check("pill 在 composer 工具行内", st.inBar);
    check("隐藏 select 真源 auto", st.modeVal === "auto", st.modeVal);

    // 2) select 与 pill 几何重合（透明覆盖生效）
    const geo = JSON.parse(await evalJs(`(() => {
      const s = document.getElementById("f-mode"), w = s.closest(".cmp-sel");
      const a = s.getBoundingClientRect(), b = w.getBoundingClientRect();
      return JSON.stringify({ sa: a.width > 0 && a.height > 0,
        cover: Math.abs(a.left - b.left) < 2 && Math.abs(a.top - b.top) < 2 &&
               a.width >= b.width - 2 && a.height >= b.height - 2,
        barH: document.querySelector(".cmp-bar")?.getBoundingClientRect().height });
    })()`));
    check("透明 select 覆盖 pill", geo.sa && geo.cover, JSON.stringify(geo));

    // 3) 切值 → face 跟随
    await evalJs(`(() => { const s = document.getElementById("f-mode");
      s.value = "expert"; s.dispatchEvent(new Event("change", { bubbles: true })); return 1; })()`);
    const face2 = await evalJs(`document.getElementById("f-mode-face").textContent`);
    check("改值后 face 跟随「专家」", face2 === "专家", face2);

    // 4) 对话模型 pill：direct 类型下显示、菜单钻取、写回真源
    const dw = JSON.parse(await evalJs(`(() => {
      const w = document.getElementById("f-direct-wrap");
      return JSON.stringify({ visible: !!w && !w.classList.contains("hidden"),
        btn: document.getElementById("f-direct-btn")?.textContent });
    })()`));
    check("direct 类型下模型 pill 显示", dw.visible === true, JSON.stringify(dw));
    check("模型 pill 初始「自动推荐」", dw.btn === "自动推荐", dw.btn);
    // 注入假厂商（本机探针环境通常没配供应商）：钻取/选定链路必须可验
    await evalJs(`(() => {
      S.providers = [
        { id: "prov-a", name: "测试厂A", api_key: "k", enabled: true,
          models: [{ name: "模型一", enabled: true }, { name: "模型二", enabled: true }] },
        { id: "prov-b", name: "测试厂B", api_key: "k", enabled: true,
          models: [{ name: "B-1", enabled: true }] },
      ];
      renderDirectModelPicker();
      return "ok";
    })()`);
    check("注入厂商后按钮仍「自动推荐」", (await evalJs(`document.getElementById("f-direct-btn").textContent`)) === "自动推荐");
    await evalJs(`toggleModelMenu(); 1`);
    const menu1 = JSON.parse(await evalJs(`(() => {
      const m = document.getElementById("cmp-model-menu");
      return JSON.stringify({ open: !m.classList.contains("hidden"),
        items: m.querySelectorAll("[data-p]").length,
        manage: !!m.querySelector("[data-manage]") });
    })()`));
    check("菜单打开且列厂商", menu1.open && menu1.items > 0, JSON.stringify(menu1));
    check("菜单底部「管理模型」入口", menu1.manage);
    // 真实坐标点击（CDP 鼠标事件走 hit-testing，能暴露透明遮挡/命中失败——
    // 程序化 .click() 会绕过这些，2026-09-21 用户真机「点厂商没反应」排查用）
    const pt = JSON.parse(await evalJs(`(() => {
      const b = document.querySelector("#cmp-model-menu [data-p='prov-a']");
      const r = b.getBoundingClientRect();
      const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      const chain = [];
      let n = el;
      while (n && n !== document.body) {
        chain.push(n.tagName + (n.id ? "#" + n.id : "") +
          (n.className && typeof n.className === "string" ? "." + n.className.split(" ").join(".") : ""));
        n = n.parentElement;
      }
      return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
        topEl: (el || {}).tagName, topId: (el || {}).id || "",
        topCls: String((el || {}).className || "").slice(0, 80), chain: chain.slice(0, 5) });
    })()`));
    console.log("    命中链:", (pt.chain || []).join("  <-  "));
    check("厂商项中心命中的是自身（无遮挡）", pt.topEl === "BUTTON" || pt.topEl === "SPAN",
      JSON.stringify(pt));
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
    await sleep(300);
    const realClick = JSON.parse(await evalJs(`(() => {
      const m = document.getElementById("cmp-model-menu");
      return JSON.stringify({ intoModels: !!m.querySelector("[data-back]"),
        pv: document.getElementById("f-direct-provider").value,
        btn: document.getElementById("f-direct-btn").textContent });
    })()`));
    check("真实坐标点厂商 → 钻取+带出模型", realClick.intoModels && realClick.pv === "prov-a" && /模型一/.test(realClick.btn),
      JSON.stringify(realClick));
    // 真实点击已在模型级：验证「随厂商推荐」选定 → 写回真源 → 收菜单
    if (realClick.intoModels) {
      const pick = await evalJs(`(() => {
        const b = document.querySelector("#cmp-model-menu [data-m]");
        if (!b) return "no-item";
        b.click();
        return JSON.stringify({
          pv: document.getElementById("f-direct-provider").value,
          mv: document.getElementById("f-direct-model-name").value,
          btn: document.getElementById("f-direct-btn").textContent,
          closed: document.getElementById("cmp-model-menu").classList.contains("hidden") });
      })()`);
      if (pick !== "no-item") {
        const d = JSON.parse(pick);
        check("选定写回 provider 真源", d.pv === "prov-a", d.pv);
        check("选完菜单收起", d.closed === true, String(d.closed));
      }
    } else {
      // 真实点击未钻入（理论不该发生）：回落程序化 click 兜底验证逻辑本身
      const drilled = await evalJs(`(() => {
        const first = document.querySelector("#cmp-model-menu [data-p]:not([data-m])");
        if (!first) return "no-prov";
        first.click();
        const m = document.getElementById("cmp-model-menu");
        return JSON.stringify({ back: !!m.querySelector("[data-back]"),
          models: m.querySelectorAll("[data-m]").length,
          pv: document.getElementById("f-direct-provider").value,
          mv: document.getElementById("f-direct-model-name").value,
          btn: document.getElementById("f-direct-btn").textContent });
      })()`);
      if (drilled !== "no-prov") {
        const d = JSON.parse(drilled);
        check("（回落）程序化钻取厂商列模型", d.back && d.models >= 1, drilled);
        check("（回落）点厂商即带出推荐模型", d.pv !== "" && d.mv !== "", drilled);
        await evalJs(`document.querySelector("#cmp-model-menu [data-m]").click(); 1`);
      }
    }
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
