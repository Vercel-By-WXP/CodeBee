/* 第 3 轮 UI 验证：Edge headless + CDP（Node 内置 WebSocket，零依赖）。
 * 打开临时服务的页面 → 断言类型下拉由 JS 填充 → 切换各设置页 →
 * 打开流程管理弹框 → 断言编排设置渲染 → 截图 → 关闭浏览器（清理进程）。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const CDP_PORT = 9333;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const edge = EDGE_CANDIDATES.find(() => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const proc = spawn(edge, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    // 拿 about:blank 页面的 ws 调试地址
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        const list = await res.json();
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
    await sleep(3500);   // 等 load + SSE 首帧 + loadFlows

    // 1) 任务类型下拉由 loadFlows → renderTypeOptions 填充
    const nTypes = await evalJs(`document.getElementById("f-type").options.length`);
    check("类型下拉 JS 填充（≥6 项）", nTypes >= 6, "实际 " + nTypes);
    const firstType = await evalJs(`document.getElementById("f-type").options[0].value`);
    check("类型下拉含自定义流程位（首项可切换）", !!firstType, firstType);

    // 2) 切换类型 → 表单区联动
    await evalJs(`
      const sel = document.getElementById("f-type");
      const reviewOpt = Array.from(sel.options).find(o => o.value === "novel");
      sel.value = "novel"; sel.dispatchEvent(new Event("change")); "ok"`);
    await sleep(300);
    const reviewShown = await evalJs(`!document.getElementById("f-review-only").classList.contains("hidden")`);
    const rubricFilled = await evalJs(`document.getElementById("f-rubric").value`);
    check("选 novel → 评审表单区显示", reviewShown === true);
    check("评审维度按流程预填", /情节/.test(rubricFilled || ""), rubricFilled);

    // 3) 编排设置页：供应商下拉渲染（无供应商时也有「不使用」选项）
    await evalJs(`switchTab("orch"); "ok"`);
    await sleep(1200);
    const orchHtml = await evalJs(`document.getElementById("orch-config").innerHTML`);
    check("编排设置渲染编排者表单", /编排者供应商/.test(orchHtml || ""), (orchHtml || "").slice(0, 120));
    const workers = await evalJs(`document.getElementById("set-workers").value`);
    check("并发数输入框已加载", Number(workers) >= 1 && Number(workers) <= 6, workers);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "r3-orch.png"),
        Buffer.from(r.result.data, "base64"));
    });

    // 4) CLI 绑定页：跨厂商链编辑器渲染（本机已装 CLI 时有卡片）
    await evalJs(`switchTab("bindings"); "ok"`);
    await sleep(1200);
    const bindHtml = await evalJs(`document.getElementById("binding-list").innerHTML`);
    check("绑定页渲染模型链编辑器", /运行时模型链/.test(bindHtml || "") && /跨厂商/.test(bindHtml || ""),
      (bindHtml || "").slice(0, 120));

    // 5) 流程管理弹框
    await evalJs(`openFlowsManager(); "ok"`);
    await sleep(500);
    const modalTitle = await evalJs(`document.getElementById("modal-title").textContent`);
    const modalRows = await evalJs(`document.querySelectorAll("#modal-body .item").length`);
    check("流程管理弹框列出全部类型", /任务类型管理/.test(modalTitle || "") && modalRows >= 6,
      modalTitle + " rows=" + modalRows);
    await evalJs(`flowForm(); "ok"`);
    await sleep(300);
    const formEngine = await evalJs(`document.getElementById("fl-engine") ? "yes" : "no"`);
    check("新建流程表单可打开", formEngine === "yes");
    await evalJs(`closeModal(); "ok"`);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "r3-flows.png"),
        Buffer.from(r.result.data, "base64"));
    });

    // 6) 侧栏左下角图标入口：设置/手机连接都是图标（无 emoji、无文字），点齿轮直接进设置页
    await evalJs(`exitSettings(); localStorage.removeItem("orch.setTab"); "ok"`);
    await sleep(300);
    const footIcons = await evalJs(`JSON.stringify({
      count: document.querySelectorAll("#btn-settings, #btn-phone-side").length,
      iconed: document.querySelectorAll("#btn-settings > svg.ico use, #btn-phone-side > svg.ico use").length,
      text: (document.getElementById("btn-settings").textContent || "").trim(),
      emoji: /[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u.test(document.querySelector(".side-foot").textContent || "")
    })`);
    const foot = JSON.parse(footIcons);
    check("左下角两个图标入口都存在", foot.count === 2, JSON.stringify(foot));
    check("设置/手机连接用 SVG 图标而非文字或 emoji", foot.iconed === 2 && foot.text === "" && !foot.emoji, JSON.stringify(foot));

    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(900);
    const afterClick = await evalJs(`JSON.stringify({
      settingsMode: document.body.classList.contains("settings-mode"),
      menuHidden: document.getElementById("ctx-menu").classList.contains("hidden"),
      menuEmpty: (document.getElementById("ctx-menu").innerHTML || "").trim() === "",
      sideVisible: getComputedStyle(document.querySelector(".side-settings")).display,
      title: document.getElementById("page-title").textContent,
      active: (document.querySelector(".set-item.active") || {}).dataset?.sub || ""
    })`);
    const ac = JSON.parse(afterClick);
    check("点齿轮直接进设置页（不再弹菜单）",
      ac.settingsMode && ac.menuHidden && ac.menuEmpty && ac.sideVisible === "flex",
      afterClick);
    check("设置页默认落到一个有效子页", ["tasks", "runs", "usage", "agents", "models", "bindings", "orch", "appearance"].includes(ac.active) && !!ac.title, afterClick);

    const navIcons = await evalJs(`JSON.stringify({
      items: document.querySelectorAll(".side-settings .set-item").length,
      iconed: document.querySelectorAll(".side-settings .set-item > svg.ico use[href^='#i-']").length,
      back: !!document.querySelector("#btn-set-back > svg.ico use"),
      texts: Array.from(document.querySelectorAll(".side-settings .set-item")).map(b => b.textContent.trim())
    })`);
    const nav = JSON.parse(navIcons);
    check("设置导航每项都有统一 SVG 图标", nav.items >= 8 && nav.iconed === nav.items && nav.back, JSON.stringify(nav));
    check("设置导航只剩纯文字标签（无 emoji 混排）",
      nav.texts.every((t) => !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{2BFF}]/u.test(t)), JSON.stringify(nav.texts));

    // 切回设置后回退：返回 Tutti 应恢复任务树
    await evalJs(`document.getElementById("btn-set-back").click(); "ok"`);
    await sleep(400);
    const backOk = await evalJs(`JSON.stringify({
      settingsMode: document.body.classList.contains("settings-mode"),
      mainVisible: getComputedStyle(document.querySelector(".side-main")).display
    })`);
    const bk = JSON.parse(backOk);
    check("返回 Tutti 恢复任务树", !bk.settingsMode && bk.mainVisible === "flex", backOk);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "r3-sidebar.png"),
        Buffer.from(r.result.data, "base64"));
    });
    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(800);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "r3-settings-nav.png"),
        Buffer.from(r.result.data, "base64"));
    });

    // 7) 服务端确认这些请求没有 5xx（控制台无异常由服务日志兜底）
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
  console.log("\n===== 第 3 轮（UI/CDP）：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
