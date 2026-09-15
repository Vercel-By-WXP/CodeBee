/* 「关于与更新」子页 UI 验证：Edge headless + CDP（零依赖，沿 ui_check.mjs 模板）。
 * 自起临时服务（TUTTI_DATA=临时目录、端口 18798）→ 打开页面 → 断言设置导航含
 * 关于与更新 → 切入子页断言版本/安装方式渲染 → 检查更新按钮可用、repo 模式
 * 升级按钮隐藏、note 提示 git pull → 启动静默查新不写 su.seen → 清理进程。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18798;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9337;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-about-"));

  // 1) 临时服务（TUTTI_DATA 隔离，绝不碰真实 data/）
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪（18798，TUTTI_DATA 隔离）", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  const consoleErrors = [];
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
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
      if (msg.method === "Runtime.consoleAPICalled" && msg.params?.type === "error") {
        consoleErrors.push(String(msg.params.args?.[0]?.value || "").slice(0, 120));
      }
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

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);   // load + SSE 首帧 + suStartupCheck 静默查新

    // 1) 设置导航含「关于与更新」，带统一 SVG 图标
    const nav = JSON.parse(await evalJs(`JSON.stringify({
      has: !!document.querySelector(".set-item[data-sub='about']"),
      icon: !!document.querySelector(".set-item[data-sub='about'] > svg.ico use[href^='#i-']"),
      text: (document.querySelector(".set-item[data-sub='about']") || {}).textContent?.trim() || ""
    })`));
    check("设置导航含「关于与更新」+ SVG 图标", nav.has && nav.icon, JSON.stringify(nav));

    // 2) 启动静默查新（repo 模式 has_update=false）不写 su.seen
    const seenAfterLoad = await evalJs(`localStorage.getItem("su.seen")`);
    check("无新版时不写 su.seen（同版本只提一次的记账键）", seenAfterLoad === null, seenAfterLoad);

    // 3) 切入关于页：subpage 显示、版本与安装方式渲染
    await evalJs(`switchTab("about"); "ok"`);
    await sleep(1200);
    const page = JSON.parse(await evalJs(`JSON.stringify({
      visible: !document.getElementById("sub-about").classList.contains("hidden"),
      title: document.getElementById("page-title").textContent,
      info: document.getElementById("su-info").textContent || "",
      note: document.getElementById("su-note").textContent || "",
      applyHidden: document.getElementById("su-apply").classList.contains("hidden"),
      restartHidden: document.getElementById("su-restart").classList.contains("hidden"),
      checkDisabled: document.getElementById("su-check").disabled
    })`));
    check("sub-about 面板显示且页签标题正确", page.visible && /关于与更新/.test(page.title), page.title);
    check("版本信息渲染（当前版本 v0.1.0 + 开发仓库模式）",
      /v0\.1\.0/.test(page.info) && /开发仓库/.test(page.info), page.info);
    check("repo 模式 note 提示 git pull", /git pull/.test(page.note), page.note);
    check("repo 模式升级按钮隐藏（永不自动升级防覆盖）", page.applyHidden && page.restartHidden);
    check("检查更新按钮可用", page.checkDisabled === false);

    // 4) 点「检查更新」→ 按钮短暂 disabled → 恢复；note 保持 git pull 提示
    await evalJs(`suCheck(); "ok"`);
    await sleep(2500);
    const afterCheck = JSON.parse(await evalJs(`JSON.stringify({
      disabled: document.getElementById("su-check").disabled,
      note: document.getElementById("su-note").textContent || ""
    })`));
    check("检查更新后按钮恢复可用", afterCheck.disabled === false);
    check("强制查新后仍提示 git pull（缓存强制刷新路径）", /git pull/.test(afterCheck.note), afterCheck.note);

    // 5) repo 模式点升级 → 后端 400 门控，前端 toast 报错而非静默
    //    （stub 掉应用内确认框：门控行为本体是后端 400 → toast 报错）
    const applyErr = await evalJs(`
      window.__uiConfirmBackup = window.uiConfirm;
      window.uiConfirm = async () => true;
      suApply().finally(() => { window.uiConfirm = window.__uiConfirmBackup; });
      "pending"`);
    await sleep(1500);
    const toastTxt = await evalJs(`document.getElementById("toast")?.textContent || ""`);
    check("repo 模式升级被门控（toast 报错，不发起升级）",
      /不支持自动升级|失败/.test(toastTxt || ""), toastTxt);

    // 6) 页签标题与导航 active 态
    const active = await evalJs(`(document.querySelector(".set-item.active") || {}).dataset?.sub`);
    check("关于页为当前活动子页", active === "about", active);

    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "about-page.png"),
        Buffer.from(r.result.data, "base64"));
    });

    check("浏览器控制台无 JS 错误", consoleErrors.length === 0, consoleErrors.join(" | "));

    // 7) 服务端复核：/api/selfupdate 与页面渲染一致
    const su = await fetch(SERVICE + "/api/selfupdate").then((r) => r.json());
    check("服务端 mode=repo / current=0.1.0 / 无更新", su.mode === "repo" && su.current === "0.1.0" && su.has_update === false,
      JSON.stringify(su));

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 关于与更新（UI/CDP）：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
