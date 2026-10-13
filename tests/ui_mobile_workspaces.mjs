/* Mobile remote homepage regression. Isolated data/service, real CDP touches;
 * no tasks are executed. Run: node tests/ui_mobile_workspaces.mjs */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { bootEdge } from "./_ui_boot.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const data = mkdtempSync(join(tmpdir(), "codebee-mobile-workspaces-"));
const port = Number(process.env.TUTTI_TEST_PORT || 18947);
const service = `http://127.0.0.1:${port}`;
const workdir = join(data, "MultiAgentOrchestration-long-workspace-name & author's project");
const runId = "r-20990101-000000-0001";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let browser, server, checks = 0;
function check(name, value) { assert.ok(value, name); checks++; console.log("  ✓ " + name); }

try {
  mkdirSync(join(data, "tasks"), { recursive: true });
  mkdirSync(join(data, "runs", runId, "steps"), { recursive: true });
  mkdirSync(workdir, { recursive: true });
  for (const [id, title, dir] of [
    ["mobile-a", "发布新版本", workdir],
    ["mobile-b", '<img src=x onerror="window.mobileInjected=1">超长任务标题验证', workdir],
    ["mobile-c", "另一工作区任务", join(data, "OtherProject")],
  ]) {
    writeFileSync(join(data, "tasks", id + ".json"), JSON.stringify({
      id, title, workdir: dir, type: "code", goal: "UI fixture", status: "done",
      created_at: "2099-01-01 00:00:00", archived: false, mode: "manual",
    }));
  }
  writeFileSync(join(data, "runs", runId, "run.json"), JSON.stringify({
    id: runId, task_id: "mobile-a", title: "发布新版本", kind: "orchestration", status: "done",
    steps: [], messages: [], created_at: "2099-01-01 00:00:00", started_at: "2099-01-01 00:00:00",
    ended_at: "2099-01-01 00:00:01", cost_usd: 0, tokens: 0, summary: "UI fixture", error: "",
  }));
  server = spawn("python", ["app/main.py", "--port", String(port)], {
    cwd: root, stdio: "ignore", env: { ...process.env, TUTTI_DATA: data },
  });
  let up = false;
  for (let i = 0; i < 60 && !up; i++) {
    await sleep(250);
    try { up = (await fetch(service + "/api/state")).ok; } catch { /* starting */ }
  }
  assert.ok(up, "isolated server started");
  browser = await bootEdge({ width: 1440, height: 1000, awaitPromise: true });
  const { send, evalJs } = browser;
  const errors = [];
  browser.ws.addEventListener("message", (e) => {
    const event = JSON.parse(e.data);
    if (event.method === "Runtime.exceptionThrown") errors.push(event.params.exceptionDetails.text);
  });
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: 'localStorage.setItem("orch.welcomed","1");' });
  const resize = (width, height = 844) => send("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: true,
  });
  const tap = async (selector) => {
    const pos = await evalJs(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      el.scrollIntoView({ block: "nearest" });
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height,
        hit: hit === el || el.contains(hit), target: hit && (hit.id || hit.className.baseVal || hit.className) };
    })()`);
    assert.ok(pos && pos.w > 0 && pos.h > 0, "touch target " + selector);
    assert.ok(pos.hit, "touch target hit " + selector + " " + JSON.stringify(pos));
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: pos.x, y: pos.y }] });
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await sleep(250);
  };
  const waitFor = async (expr) => {
    for (let i = 0; i < 50; i++) { if (await evalJs(expr)) return true; await sleep(100); }
    return false;
  };
  const screenshot = async (name) => {
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const output = join(root, "output", "playwright");
    mkdirSync(output, { recursive: true });
    writeFileSync(join(output, name), Buffer.from(shot.result.data, "base64"));
  };

  await resize(390);
  await send("Page.navigate", { url: service });
  check("工作区首页读取真实任务分组", await waitFor('document.querySelectorAll(".mobile-workspace").length === 2'));
  check("工作区路径/标题按文本转义", await evalJs('!window.mobileInjected && !document.querySelector("#mobile-workspace-list img")'));
  for (const width of [320, 390, 430, 768, 900]) {
    await resize(width); await sleep(100);
    const layout = await evalJs(`(() => {
      const main = document.getElementById("mainwrap").getBoundingClientRect();
      const home = document.getElementById("mobile-workspaces").getBoundingClientRect();
      return { x: main.x, w: main.width, y: home.y, home: home.width,
        overflow: document.documentElement.scrollWidth > innerWidth,
        hiddenRail: getComputedStyle(document.querySelector(".side-rail")).visibility === "hidden" };
    })()`);
    check(`${width}px 单列占满视口且导航无露出`, layout.x === 0 && layout.w === width && layout.hiddenRail);
    check(`${width}px 首页无横向溢出或巨大留白`, !layout.overflow && layout.y < 120 && layout.home > width - 50);
  }
  await resize(390);
  await tap('.mobile-workspace:has(.mobile-task[data-task="mobile-a"]) > summary');
  check("完成状态显示文字", await evalJs('document.querySelector(".mobile-task[data-task=mobile-a]").textContent.includes("完成")'));
  await screenshot("mobile-workspaces.png");
  await tap('.mobile-workspace:has(.mobile-task[data-task="mobile-a"]) > summary');
  const wasOpen = await evalJs('document.querySelector(".mobile-workspace:has(.mobile-task[data-task=mobile-a])").open');
  await evalJs('S.sideSig = ""; renderSideTasks();');
  check("状态刷新保留手机卡片展开态", await evalJs('document.querySelector(".mobile-workspace:has(.mobile-task[data-task=mobile-a])").open') === wasOpen);
  await tap('.mobile-workspace-card:has(.mobile-task[data-task="mobile-a"]) .mobile-workspace-new');
  assert.equal(await evalJs('document.getElementById("f-workdir").value'), workdir, "工作区加号带入完整路径");
  check("工作区加号带入完整路径", true);
  for (const width of [320, 390, 430]) {
    await resize(width); await sleep(100);
    const composer = await evalJs(`(() => {
      const b = document.getElementById("btn-create"), r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      const ta = document.getElementById("f-goal");
      return { send: r.width >= 44 && r.height >= 44 && (hit === b || b.contains(hit)),
        font: parseFloat(getComputedStyle(ta).fontSize), y: ta.getBoundingClientRect().y,
        rect: { x: r.x, y: r.y, w: r.width, h: r.height }, hit: hit && (hit.id || hit.className.baseVal || hit.className),
        overflow: document.querySelector("main").scrollWidth > document.querySelector("main").clientWidth };
    })()`);
    assert.ok(composer.font >= 16 && composer.send, JSON.stringify(composer));
    check(`${width}px 输入字号不触发 iOS 放大、发送可触摸`, true);
    check(`${width}px 输入页无横向溢出且首屏可输入`, !composer.overflow && composer.y < 300);
  }
  await resize(390);
  await screenshot("mobile-composer.png");
  await tap("#btn-menu");
  check("导航轨在抽屉中可触摸", await evalJs('getComputedStyle(document.querySelector(".side-rail")).visibility === "visible"'));
  await tap('.rail-btn[title="设置"]');
  check("抽屉导航进设置并收起", await evalJs('document.body.classList.contains("settings-mode") && document.body.classList.contains("side-collapsed")'));
  await tap("#btn-menu");
  await tap('.rail-btn[data-rail-page="tasks"]');
  await tap("#btn-mobile-new");
  await tap("#btn-mobile-workspaces");
  await evalJs('document.querySelector(".mobile-workspace:has(.mobile-task[data-task=mobile-a])").open = true;');
  await tap('.mobile-task[data-task="mobile-a"]');
  check("工作区任务可打开原有详情", await waitFor('S.detailTaskKey === "mobile-a" && !document.getElementById("run-detail").classList.contains("hidden")'));
  await evalJs('exitSettings();');
  await send("Emulation.clearDeviceMetricsOverride"); await sleep(200);
  await evalJs('document.body.classList.remove("side-collapsed");');
  check("恢复桌面三列并显示原输入页", await evalJs('getComputedStyle(document.getElementById("app")).gridTemplateColumns.startsWith("52px ") && getComputedStyle(document.getElementById("mobile-workspaces")).display === "none" && getComputedStyle(document.getElementById("btn-mobile-workspaces")).display === "none" && document.querySelector(".composer").getBoundingClientRect().width > 0'));
  await screenshot("mobile-desktop-regression.png");
  await resize(390); await sleep(150);
  check("桌面缩回手机自动收起导航", await evalJs('document.body.classList.contains("side-collapsed")'));
  await evalJs('S.state.tasks = []; S.state.runs = []; S.state.task_latest = {}; S.sideSig = ""; renderSideTasks();');
  check("空任务自动进入输入页", await evalJs('!document.getElementById("sub-tasks").classList.contains("mobile-has-workspaces") && document.querySelector(".composer").getBoundingClientRect().width > 0'));
  check("页面无未捕获 JS 错误", errors.length === 0);
  const navigation = spawn(process.execPath, [join(root, "tests", "ui_mobile.mjs")], {
    cwd: root, stdio: "inherit", env: { ...process.env, SERVICE: service },
  });
  const navigationExit = await new Promise((resolve, reject) => {
    navigation.once("exit", resolve);
    navigation.once("error", reject);
  });
  check("原有移动端图标/设置/手机连接回归", navigationExit === 0);
  console.log(`\n手机工作台：${checks} 项通过`);
} finally {
  if (browser) await browser.close();
  if (server) {
    const stopped = new Promise((resolve) => server.once("exit", resolve));
    server.kill(); await stopped;
  }
  rmSync(data, { recursive: true, force: true });
}
