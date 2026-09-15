/* 检查器（右侧详情）与新建表单互斥探针：照 ui_check.mjs 模板。
 * 场景 = 用户反馈：停在「要完成什么？」新建表单时，右侧任务详情不应自动展示。
 * 临时数据目录起服务（18797），用页内 stub 任务驱动 openInspector/exitSettings/
 * syncInspectorVis 的四种转换，不写任何服务端数据。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18797";
const PORT = 18797;
const CDP_PORT = 9335;
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
  const tmp = mkdtempSync(join(tmpdir(), "tutti-inspprobe-"));
  const pyProc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT },
    cwd: ROOT, stdio: "ignore",
  });
  let svcUp = false;
  for (let i = 0; i < 40 && !svcUp; i++) {
    await sleep(500);
    try { svcUp = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* 未就绪 */ }
  }
  check("服务启动（临时数据目录 :18797）", svcUp);

  const edge = EDGE_CANDIDATES.find(() => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const browser = spawn(edge, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

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
    const inspShown = `!document.getElementById("inspector").classList.contains("hidden")
      && document.body.classList.contains("inspector-open")`;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    // 页内 stub 一个「有历史运行」的任务（不动服务端数据），模拟用户点开过详情
    await evalJs(`
      S.state = Object.assign({}, S.state, {
        tasks: [{ id: "probe-task" }],
        task_latest: { "probe-task": { status: "done" } } });
      localStorage.setItem("orch.inspector", "probe-task");
      openInspector("probe-task"); "ok"`);
    await sleep(300);
    check("点开任务 → 检查器滑出（现状前提）", await evalJs(inspShown) === true);

    // 1) 核心诉求：＋新任务（exitSettings）回新建表单 → 检查器收起
    await evalJs(`exitSettings(); "ok"`);
    await sleep(200);
    check("回新建表单 → 检查器自动收起", await evalJs(inspShown) === false);
    check("表单可见（主视图仍是 sub-tasks）", await evalJs(`!document.getElementById("sub-tasks").classList.contains("hidden")`) === true);

    // 2) F5/恢复路径： inspKey 还在（选中不丢），表单态 syncInspectorVis 不滑出
    await evalJs(`S.inspKey = "probe-task"; syncInspectorVis(); "ok"`);
    await sleep(150);
    check("选中保留但表单态不滑出", await evalJs(`S.inspKey === "probe-task"`) === true
      && (await evalJs(inspShown)) === false);

    // 3) 表单页手动点树里的任务：openInspector 直接开，不被收
    await evalJs(`openInspector("probe-task")`);
    await sleep(200);
    check("表单页手动点任务 → 检查器打开", await evalJs(inspShown) === true);

    // 4) 运行详情仍是任务上下文：sub-runs 可见时 syncInspectorVis 滑回
    await evalJs(`
      document.getElementById("sub-tasks").classList.add("hidden");
      document.getElementById("sub-runs").classList.remove("hidden");
      S.inspKey = "probe-task"; syncInspectorVis(); "ok"`);
    await sleep(150);
    check("运行详情（任务上下文）→ 检查器滑回", await evalJs(inspShown) === true);

    // 5) 设置子页照旧收起（回归）
    await evalJs(`
      document.getElementById("sub-runs").classList.add("hidden");
      document.getElementById("sub-orch").classList.remove("hidden");
      document.body.classList.add("settings-mode");
      syncInspectorVis(); "ok"`);
    await sleep(150);
    check("设置子页 → 检查器照旧收起（回归）", await evalJs(inspShown) === false);

    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r4-insp-composer.png"), Buffer.from(shot.result.data, "base64"));
  } finally {
    browser.kill();
    pyProc.kill();
    await sleep(500);
    if (results.every((r) => r.ok)) rmSync(tmp, { recursive: true, force: true });
    else console.log("（失败现场保留：%s）", tmp);
  }

  const fail = results.filter((r) => !r.ok).length;
  console.log("\n===== 检查器互斥探针：%d 通过 / %d 失败 =====", results.length - fail, fail);
  if (fail) process.exit(1);
}

main().catch((e) => { console.error("探针异常：", e); process.exit(1); });
