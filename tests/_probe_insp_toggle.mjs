/* 顶栏「任务详情」开合按钮探针：照 ui_check.mjs 模板，临时服务 18797。
 * 验证 toggleInspector 的开/合/兜底/提示与箭头状态变量，以及与新建表单互斥的既有规则。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18797";
const PORT = 18797;
const CDP_PORT = 9336;
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
  const tmp = mkdtempSync(join(tmpdir(), "tutti-tglprobe-"));
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
    const btnState = `(() => { const b = document.getElementById("btn-insp");
      return { title: b.title,
        closed: getComputedStyle(b).getPropertyValue("--insp-chev-closed").trim(),
        open: getComputedStyle(b).getPropertyValue("--insp-chev-open").trim() }; })()`;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    check("按钮存在且可见", await evalJs(`(() => { const b = document.getElementById("btn-insp");
      const r = b.getBoundingClientRect(); return r.width > 20 && r.height > 20; })()`) === true);
    check("按钮贴动作组最右（前=日夜切换，后=连接状态）",
      await evalJs(`(() => { const b = document.getElementById("btn-insp");
        return b.previousElementSibling.id === "btn-theme" && b.nextElementSibling.id === "conn"; })()`) === true);
    check("按钮与相邻胶囊同款（同类名，盒尺寸一致）",
      await evalJs(`(() => { const a = document.getElementById("btn-insp").getBoundingClientRect();
        const c = document.getElementById("btn-theme").getBoundingClientRect();
        return document.getElementById("btn-insp").className === document.getElementById("btn-theme").className
          && Math.abs(a.height - c.height) <= 1; })()`) === true);
    const st0 = await evalJs(btnState);
    check("初始态：关着，›展开箭头可见，提示=打开任务详情",
      st0.closed === "inline" && st0.open === "none" && /打开任务详情/.test(st0.title), JSON.stringify(st0));

    // 1) 一个任务都没有：点开 → toast 提示，面板不滑出
    await evalJs(`S.state = Object.assign({}, S.state, { tasks: [], task_latest: {} }); toggleInspector(); "ok"`);
    await sleep(200);
    const toastTxt = await evalJs(`(document.getElementById("toast") || {}).textContent || ""`);
    check("无任务时点击 → 提示「还没有可展示的任务详情」", /还没有可展示的任务详情/.test(toastTxt), toastTxt);
    check("无任务时面板不滑出", await evalJs(`document.body.classList.contains("inspector-open")`) === false);

    // 2) stub 一个跑过的任务：点开 → 面板滑出 + 箭头变量翻转 + 提示变收起
    await evalJs(`
      S.state = Object.assign({}, S.state, {
        tasks: [{ id: "probe-a" }, { id: "probe-b" }],
        task_latest: { "probe-a": { status: "done" }, "probe-b": { status: "running" } } });
      toggleInspector(); "ok"`);
    await sleep(300);
    const st1 = await evalJs(btnState);
    check("有任务时点击 → 面板滑出", await evalJs(`document.body.classList.contains("inspector-open")`) === true);
    check("开着：箭头变量=inline(‹收起)，提示=收起任务详情",
      st1.open === "inline" && /收起任务详情/.test(st1.title), JSON.stringify(st1));
    check("兜底选中了最近跑过的任务", await evalJs(`S.inspKey`) === "probe-a");

    // 3) 再点 → 收起
    await evalJs(`toggleInspector(); "ok"`);
    await sleep(200);
    const st2 = await evalJs(btnState);
    check("再点 → 面板收起、变量与提示复位",
      (await evalJs(`document.body.classList.contains("inspector-open")`) === false)
      && st2.closed === "inline" && /打开任务详情/.test(st2.title), JSON.stringify(st2));

    // 4) 在新建表单页手动打开是允许的（用户主动行为）
    await evalJs(`toggleInspector(); "ok"`);
    await sleep(200);
    check("表单页手动打开面板正常", await evalJs(`document.body.classList.contains("inspector-open")`) === true);
    await evalJs(`toggleInspector(); "ok"`);

    // 5) 回归：exitSettings（＋新任务路径）仍会自动收起
    await evalJs(`openInspector("probe-b"); "ok"`);
    await sleep(200);
    await evalJs(`exitSettings(); "ok"`);
    await sleep(200);
    check("回归：进新建表单仍自动收起", await evalJs(`document.body.classList.contains("inspector-open")`) === false);

    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r4-insp-toggle.png"), Buffer.from(shot.result.data, "base64"));
  } finally {
    browser.kill();
    pyProc.kill();
    await sleep(500);
    if (results.every((r) => r.ok)) rmSync(tmp, { recursive: true, force: true });
    else console.log("（失败现场保留：%s）", tmp);
  }

  const fail = results.filter((r) => !r.ok).length;
  console.log("\n===== 开合按钮探针：%d 通过 / %d 失败 =====", results.length - fail, fail);
  if (fail) process.exit(1);
}

main().catch((e) => { console.error("探针异常：", e); process.exit(1); });
