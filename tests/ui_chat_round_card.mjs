/* 对话结果卡核验（2026-09-22 用户反馈两连）：
 *   A) 每轮收尾卡说「本轮完成」不再喊「任务完成」——追话一直都在同一个任务里，
 *      卡头喊「任务完成」会被读成又建了一个新任务；
 *   B) HTML 成品的文件 chip 带「运行展示」按钮：弹窗内 iframe 真跑 /preview 挂载
 *      （源码弹窗看不出页面长什么样）；非网页成品不带该按钮，预览/下载原样。
 * 前置：TUTTI_DATA 临时目录起 18912 服务（本脚本自起）；种子 _seed_chat_round_fixture.py。
 */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18912;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9363;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => { try { execSync(`cmd /c if exist "${p}" echo ok`, { stdio: "pipe" }); return true; } catch (e) { return false; } });
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口 " + PORT + " 已被占用，先清理残留服务再跑");
    process.exit(2);
  } catch (e) { /* 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-round-"));
  const seed = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_chat_round_fixture.py")],
    { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let seedOut = "";
  seed.stdout.on("data", (d) => { seedOut += String(d); });
  await new Promise((res) => seed.on("exit", res));
  const runId = (seedOut.match(/RUN_ID=(\S+)/) || [])[1] || "";
  const taskId = (seedOut.match(/TASK_ID=(\S+)/) || [])[1] || "";
  check("种子直连 run 就绪", !!runId && !!taskId, seedOut.slice(0, 200));

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  check("临时服务启动", up);

  // /preview 挂载就绪（后端口径先自证，前端只管按钮与弹窗）
  const pv = await fetch(SERVICE + "/api/runs/" + runId + "/preview").then((r) => r.json());
  check("后端预览挂载可用", pv && pv.ok === true && !!pv.base, JSON.stringify(pv).slice(0, 200));

  const profile = mkdtempSync(join(tmpdir(), "tutti-round-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--disable-sync", "--disable-extensions", `--user-data-dir=${profile}`,
    `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
  let shot = "";
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless + CDP 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 25000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      else if (m.method === "Page.javascriptDialogOpening")
        send("Page.handleJavaScriptDialog", { accept: true });
    };
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    // 首启欢迎层会挡住详情页视觉：先记账已欢迎，刷新后弹层不再出现
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`try { localStorage.setItem("orch.welcomed", "1"); } catch (e) {} "ok"`);
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    /* ---- A) 结果卡：本轮完成 + HTML chip 带运行按钮 ---- */
    // 走真实用户路径：侧栏任务行 → 任务级详情（直连默认落「对话」分区）。
    // 直接调 openRun 会跳过 showDetailInMain，详情渲染在被隐藏的容器里（假可见）。
    await evalJs(`sideOpenTask(${JSON.stringify(taskId)}); "ok"`);
    await sleep(2500);
    const a = JSON.parse(await evalJs(`JSON.stringify({
      cardLabel: (document.querySelector("#rd-chat-flow .chat-result .cr-head")||{}).textContent || "",
      chips: document.querySelectorAll("#rd-chat-flow .chat-result .file-chip").length,
      runBtns: document.querySelectorAll('#rd-chat-flow .chat-result .chip-btn[title="运行展示"]').length,
      prevBtns: document.querySelectorAll('#rd-chat-flow .chat-result .chip-btn[title="预览"]').length,
    })`));
    check("结果卡收尾标签=本轮完成", a.cardLabel.includes("本轮完成") && !a.cardLabel.includes("任务完成"), a.cardLabel);
    check("产出文件两个 chip（html+md）", a.chips === 2, String(a.chips));
    check("仅 HTML chip 带运行展示按钮", a.runBtns === 1, String(a.runBtns));
    check("预览按钮两个都在", a.prevBtns === 2, String(a.prevBtns));

    // 详情层几何可见（回归「直接调 openRun 渲染进隐藏容器」的假可见陷阱：
    // 测试必须走真实路径 sideOpenTask，并断言卡片 bounding box 非 0）
    const vis = JSON.parse(await evalJs(`JSON.stringify({
      card: (() => { const el = document.querySelector("#rd-chat-flow .chat-result"); if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })(),
    })`));
    check("结果卡几何可见（非隐藏容器假可见）", vis.card === true, JSON.stringify(vis));

    const shot0 = await send("Page.captureScreenshot", { format: "png" }).catch(() => null);
    const shot0B64 = shot0 && shot0.result && shot0.result.data;
    if (shot0B64) {
      const { writeFileSync } = await import("node:fs");
      writeFileSync(join(ROOT, "tests", "_out", "ui_chat_round_card_list.png"),
        Buffer.from(shot0B64, "base64"));
    }

    /* ---- B) 点运行：弹窗 iframe 真跑 /preview 挂载 ---- */
    await evalJs(`(document.querySelector('#rd-chat-flow .chat-result .chip-btn[title="运行展示"]')||{}).click(); "ok"`);
    await sleep(2500);
    const b = JSON.parse(await evalJs(`JSON.stringify((() => {
      const pop = document.getElementById("file-pop");
      const fr = document.querySelector("#file-pop .fp-frame");
      let inner = "";
      try { inner = fr ? (fr.contentDocument || {}).body?.textContent || "" : ""; } catch (e) { inner = "ERR:" + e.message; }
      return {
        open: pop && !pop.classList.contains("hidden"),
        hasFrame: !!fr,
        src: fr ? fr.src : "",
        inner,
      };
    })())`));
    check("弹窗打开且内嵌运行 iframe", b.open === true && b.hasFrame === true, JSON.stringify(b));
    check("iframe 指向 /preview 挂载", b.src.includes("/preview/" + runId + "/"), b.src);
    check("iframe 里页面真的跑起来了", b.inner.includes("页面已运行"), b.inner.slice(0, 120));

    const s1 = await send("Page.captureScreenshot", { format: "png" }).catch(() => null);
    const shotB64 = s1 && s1.result && s1.result.data;
    if (shotB64) {
      const { writeFileSync } = await import("node:fs");
      shot = join(ROOT, "tests", "_out", "ui_chat_round_card.png");
      writeFileSync(shot, Buffer.from(shotB64, "base64"));
    }
    await evalJs(`window.filePopClose && filePopClose(); "ok"`);

    /* ---- C) 预览按钮仍是源码弹窗（契约不变）---- */
    await evalJs(`(document.querySelector('#rd-chat-flow .chat-result .chip-btn[title="预览"]')||{}).click(); "ok"`);
    await sleep(1500);
    const c = JSON.parse(await evalJs(`JSON.stringify({
      open: (() => { const p = document.getElementById("file-pop"); return p && !p.classList.contains("hidden"); })(),
      hasFrame: !!document.querySelector("#file-pop .fp-frame"),
      hasCode: !!document.querySelector("#file-pop .fp-code"),
    })`));
    check("预览按钮=源码弹窗（无 iframe）", c.open === true && c.hasCode === true && c.hasFrame === false, JSON.stringify(c));
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${proc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${svc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r).length;
  console.log("\n===== 对话结果卡（UI/CDP）：" + (results.length - bad) + " 通过 / " + bad + " 失败 =====");
  if (shot) console.log("截图：" + shot);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
