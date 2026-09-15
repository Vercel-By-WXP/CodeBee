/* 应用内弹框（#ask / toast）运行时验证：Edge headless + CDP，零依赖。
 * 不依赖截图：全部用 DOM 状态、getBoundingClientRect、computed style、
 * Promise 取值断言。临时数据目录 + 独立端口，不碰真实 data/ 与 8765。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18814;
const CDP_PORT = 9338;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-ask-"));
  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    // 服务就绪
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18814)", up);

    // CDP 连接
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    // 页面加载健康度（init 是否跑完——接线挂在 init 末段）
    const health = await evalJson(`(() => ({
      hasAsk: !!document.getElementById("ask"),
      hasHelpers: typeof uiConfirm === "function" && typeof uiPrompt === "function" && typeof toast === "function",
      wired: typeof _askResolve !== "undefined",
    }))()`);
    check("页面加载：#ask 骨架存在", health && health.hasAsk);
    check("helpers 就位 (uiConfirm/uiPrompt/toast)", health && health.hasHelpers);
    check("_askResolve 作用域可达（app.js 非模块脚本）", health && health.wired);

    const R = await evalJson(`(async () => {
      const $ = (id) => document.getElementById(id);
      const native = [];
      for (const f of ["alert", "confirm", "prompt"]) {
        window[f] = function () { native.push(f); return f === "confirm" ? true : "x"; };
      }
      const out = { nativeCalls: 0 };

      // 1) 确认框：标题/正文/按钮语义/几何/遮罩
      const p1 = uiConfirm("删除该任务及其全部运行记录？不可恢复。", { ok: "删除", danger: true });
      await new Promise((r) => setTimeout(r, 80));
      const ask = $("ask");
      out.visible = !ask.classList.contains("hidden");
      out.title = $("ask-title").textContent;
      out.body = document.querySelector("#ask-body .ask-msg").textContent;
      out.yesText = $("ask-yes").textContent;
      out.yesCls = $("ask-yes").className;
      out.cancelText = $("ask-no").textContent;
      const rect = ask.querySelector(".ask-modal").getBoundingClientRect();
      out.cx = rect.left + rect.width / 2 - innerWidth / 2;
      out.cy = rect.top + rect.height / 2 - innerHeight / 2;
      out.w = rect.width;
      out.zAsk = getComputedStyle(ask).zIndex;
      out.scrollLocked = document.body.classList.contains("ask-open");
      out.preWrap = getComputedStyle(document.querySelector("#ask-body .ask-msg")).whiteSpace;

      // 2) 取消 → false 且关闭
      $("ask-no").click();
      out.vCancel = await p1;
      out.hiddenAfter = ask.classList.contains("hidden");
      out.scrollUnlocked = !document.body.classList.contains("ask-open");

      // 3) 确定 → true
      const p2 = uiConfirm("x", { ok: "删除", danger: true });
      await new Promise((r) => setTimeout(r, 30));
      $("ask-yes").click();
      out.vOk = await p2;

      // 4) Esc → false；Enter → true（document 级 keydown）
      const p3 = uiConfirm("esc");
      await new Promise((r) => setTimeout(r, 30));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      out.vEsc = await p3;
      const p4 = uiConfirm("enter");
      await new Promise((r) => setTimeout(r, 30));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      out.vEnter = await p4;

      // 5) 输入框：预填、聚焦、回车取值（trim）、取消 → null
      const p5 = uiPrompt("重命名任务", "旧名字");
      await new Promise((r) => setTimeout(r, 30));
      const inp = $("ask-input");
      out.prefill = inp.value;
      out.focused = document.activeElement === inp;
      inp.value = "  新名字  ";
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      out.vPrompt = await p5;
      const p6 = uiPrompt("重命名任务", "x");
      await new Promise((r) => setTimeout(r, 30));
      $("ask-no").click();
      out.vPromptCancel = await p6;

      // 6) toast：普通/错误样式
      toast("已写入：glm-5.3-flash");
      await new Promise((r) => setTimeout(r, 80));
      const t = $("toast");
      out.toastShow = t.classList.contains("show");
      out.toastText = t.textContent;
      toast("保存失败：x", true);
      await new Promise((r) => setTimeout(r, 30));
      out.toastBad = t.classList.contains("bad");

      // 7) 叠层：#ask 在 #modal 之上
      openModal("测试", "<p>hi</p>", "");
      const p7 = uiConfirm("叠层测试");
      await new Promise((r) => setTimeout(r, 30));
      out.zModal = getComputedStyle($("modal")).zIndex;
      out.aboveModal = parseInt(out.zAsk) > parseInt(out.zModal);
      $("ask-yes").click(); await p7; closeModal();

      out.nativeCalls = native.length;
      return out;
    })()`);

    if (!R || R.__err) { check("浏览器端评估执行", false, JSON.stringify(R)); }
    else {
      check("原生 alert/confirm/prompt 全程零调用", R.nativeCalls === 0, R.nativeCalls);
      check("确认框打开：#ask 可见", R.visible);
      check("默认标题「确认操作」", R.title === "确认操作", R.title);
      check("正文渲染完整（含不可恢复字样）", (R.body || "").includes("不可恢复"), R.body);
      check("语义按钮：确定=「删除」且 danger 样式", R.yesText === "删除" && R.yesCls.includes("danger"), R.yesText + "/" + R.yesCls);
      check("取消按钮文案", R.cancelText === "取消", R.cancelText);
      check("几何：水平居中(|Δcx|<40)", Math.abs(R.cx) < 40, R.cx);
      check("几何：垂直居中(|Δcy|<90)", Math.abs(R.cy) < 90, R.cy);
      check("几何：紧凑宽度 280~460", R.w > 280 && R.w <= 460, R.w);
      check("z-index：ask=250 高于 modal", R.aboveModal && R.zAsk === "250", R.zAsk + " vs " + R.zModal);
      check("打开时锁定页面滚动", R.scrollLocked && R.scrollUnlocked);
      check("正文 pre-wrap（\\n 生效）", R.preWrap === "pre-wrap", R.preWrap);
      check("取消 → Promise false 且关闭", R.vCancel === false && R.hiddenAfter);
      check("确定 → Promise true", R.vOk === true);
      check("Esc → false / Enter → true", R.vEsc === false && R.vEnter === true);
      check("输入框预填+自动聚焦", R.prefill === "旧名字" && R.focused);
      check("回车取值（trim 生效）", R.vPrompt === "新名字", R.vPrompt);
      check("输入框取消 → null", R.vPromptCancel === null);
      check("toast 展示与文案", R.toastShow && (R.toastText || "").includes("已写入"), R.toastText);
      check("toast bad 样式（错误态）", R.toastBad);
    }
    check("无未捕获 JS 异常", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 200));
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(600);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\nFAILED ${bad}/${results.length}` : `\nOK ${results.length}/${results.length}`);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
