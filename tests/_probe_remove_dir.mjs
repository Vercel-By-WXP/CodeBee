/* 探针：文件夹右键「移除该文件夹」全链路——弹确认框→点移除→任务归档。
 * 临时数据目录 + 临时服务（18799/CDP 9341，避开并行测试），跑完清理。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18799;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9341;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TASK = "task-ctx";

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 400)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (path, opts = {}) => {
  const r = await fetch(SERVICE + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let j = null;
  try { j = await r.json(); } catch (e) { /* ignore */ }
  return { code: r.status, json: j };
};

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口 " + PORT + " 已被占用，先处理再跑"); process.exit(2);
  } catch (e) { /* 空闲 */ }
  // CDP 口也要预检：残留 Edge 占着口时新实例绑不上，/json/list 会驱动别人的浏览器
  try {
    execSync(`netstat -ano | findstr ":${CDP_PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("CDP 口 " + CDP_PORT + " 已被占用（残留 Edge？按 PID 清理后再跑）"); process.exit(2);
  } catch (e) { /* 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-rm-"));
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_ctx_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });
  // 额外造一个任务，稍后经 API 归档——得到「纯归档文件夹」复现用户场景
  const archWd = join(dataDir, "archived-dir");
  const { writeFileSync, mkdirSync } = await import("node:fs");
  mkdirSync(archWd, { recursive: true });
  writeFileSync(join(dataDir, "tasks", "task-arch.json"),
    JSON.stringify({ id: "task-arch", title: "纯归档夹核验", type: "novel", goal: "核验",
      workdir: archWd, status: "done", archived: false,
      created_at: "2026-09-16 10:00:00" }, null, 2), "utf8");

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
    cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  check("临时服务启动（端口 " + PORT + "）", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-rm-edge-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync", "--disable-extensions",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
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
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 右键侧栏文件夹行 */
    const step1 = await evalJs(`(async () => {
      const dir = document.querySelector('#side-tasks .sdir[data-dir]');
      if (!dir) return JSON.stringify({ err: "no sdir" });
      const dirName = dir.dataset.dir;
      dir.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }));
      await new Promise(r => setTimeout(r, 250));
      const menu = document.getElementById("ctx-menu");
      const labels = [...menu.querySelectorAll(".ctx-item")].map(x => x.textContent.trim());
      return JSON.stringify({ dirName, labels, hidden: menu.classList.contains("hidden") });
    })()`);
    const s1 = JSON.parse(step1);
    check("文件夹右键菜单弹出且含「移除该文件夹」",
      !s1.err && !s1.hidden && s1.labels.includes("移除该文件夹"), step1);

    /* 点「移除该文件夹」→ 应用内确认框 */
    await evalJs(`(async () => {
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "移除该文件夹");
      item.click();
    })()`);
    await sleep(1000);
    const askState = await evalJs(`(() => {
      const dlg = document.getElementById("ask");
      const shown = dlg && !dlg.classList.contains("hidden");
      return JSON.stringify({ shown,
        msg: shown ? (document.getElementById("ask-body")||{}).textContent : "",
        console: window.__errs || [] });
    })()`);
    const as = JSON.parse(askState);
    check("点「移除该文件夹」弹出确认框", as.shown, askState);
    check("确认框文案含「移出侧栏」", /移出侧栏/.test(as.msg || ""), as.msg);

    /* 点「移除」确认 */
    if (as.shown) {
      await evalJs(`document.getElementById("ask-yes").click(); true`);
      await sleep(2500);
      const st = await fetch(SERVICE + "/api/state").then((r) => r.json());
      const t = (st.tasks || []).find((x) => x.id === TASK);
      const at = (st.archived_tasks || []).find((x) => x.id === TASK);
      check("确认后任务进入已归档列表", !!at && !t, JSON.stringify({ t: !!t, at: !!at }));
      // SSE 推送按 2s 桶轮询版本号：轮询 DOM 等侧栏重绘，最坏 10s。
      // 只查目标目录的文件夹——无主管理运行恒渲染成「其他」，不能作为消失判据
      let sdirGone = false, waited = 0;
      while (waited < 10000) {
        await sleep(1000); waited += 1000;
        sdirGone = await evalJs(
          `!document.querySelector('#side-tasks .sdir[data-dir=' + JSON.stringify(${JSON.stringify(s1.dirName)}) + ']')`);
        if (sdirGone) break;
      }
      check("侧栏目标文件夹消失", sdirGone, "等了 " + waited + "ms 仍在");

      /* ---- 场景 B（用户实际遇到的）：纯归档文件夹 + 「显示已归档」开关打开，
       *       右键「移除该文件夹」此前收集到空 ids 静默返回；现在应弹框并在
       *       确认后把目录从侧栏隐藏（新建任务到该目录可恢复） ---- */
      await api(`/api/tasks/task-arch/archive`, { method: "POST", body: { archived: true } });
      await evalJs(`document.getElementById("btn-side-arch").click(); true`);
      await sleep(2000);
      const dirSelB = `'#side-tasks .sdir[data-dir=' + JSON.stringify(${JSON.stringify(archWd)}) + ']'`;
      const dirBack = await evalJs(`!!document.querySelector(${dirSelB})`);
      check("打开「显示已归档」后纯归档文件夹灰显回侧栏", dirBack);
      await evalJs(`(async () => {
        const dir = document.querySelector(${dirSelB});
        dir.dispatchEvent(new MouseEvent("contextmenu",
          { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }));
        await new Promise(r => setTimeout(r, 250));
        [...document.querySelectorAll("#ctx-menu .ctx-item")]
          .find(x => x.textContent.trim() === "移除该文件夹").click();
      })()`);
      await sleep(1000);
      const askShown2 = await evalJs(
        `(() => { const d = document.getElementById("ask");
          return d && !d.classList.contains("hidden"); })()`);
      check("纯归档文件夹也弹确认框（此前这里静默无反应）", askShown2);
      if (askShown2) {
        await evalJs(`document.getElementById("ask-yes").click(); true`);
        let gone2 = false, w2 = 0;
        while (w2 < 10000) {
          await sleep(1000); w2 += 1000;
          gone2 = await evalJs(`!document.querySelector(${dirSelB})`);
          if (gone2) break;
        }
        check("纯归档文件夹确认后也从侧栏消失", gone2, "等了 " + w2 + "ms 仍在");
        // 恢复路径：新建任务到该目录 → 自动解除隐藏（archWd 应从隐藏表移出；
        // 场景 A 移除的 sandbox-workdir 留在表里是预期行为）
        await evalJs(`(typeof newTaskInDir === "function") && newTaskInDir(${JSON.stringify(archWd)}); true`);
        await sleep(500);
        const unhidden = await evalJs(
          `!JSON.parse(localStorage.getItem("tutti.hiddenDirs") || "[]").includes(${JSON.stringify(archWd)})`);
        check("在该目录新建任务自动解除隐藏", unhidden,
          await evalJs(`localStorage.getItem("tutti.hiddenDirs")`));

        /* ---- 场景 C（服务重启窗口实测案）：归档请求网络层失败时文件夹绝不能
         *       照旧从侧栏消失——藏是 localStorage 假成功，服务端没归档，手机端
         *       等其他设备照样看得到。stub fetch 让 /archive 全拒 → 应弹错误
         *       toast 且文件夹留在侧栏、隐藏表不动；恢复 fetch 重试 → 正常隐藏。 ---- */
        const dirSelC = dirSelB;
        // unhideSideDir 只清签名不立即重绘，等下一轮 poll 把文件夹画回来
        let dirBack3 = false, w4 = 0;
        while (w4 < 10000) {
          await sleep(1000); w4 += 1000;
          dirBack3 = await evalJs(`!!document.querySelector(${dirSelC})`);
          if (dirBack3) break;
        }
        check("场景C：解除隐藏后文件夹重绘回侧栏", dirBack3, "等了 " + w4 + "ms 仍在");
        await evalJs(`(async () => {
          window.__origFetch = window.fetch;
          window.fetch = (url, opts) => {
            if (String(url).includes("/archive"))
              return Promise.reject(new TypeError("Failed to fetch"));
            return window.__origFetch(url, opts);
          };
          const dir = document.querySelector(${dirSelC});
          dir.dispatchEvent(new MouseEvent("contextmenu",
            { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }));
          await new Promise(r => setTimeout(r, 250));
          [...document.querySelectorAll("#ctx-menu .ctx-item")]
            .find(x => x.textContent.trim() === "移除该文件夹").click();
        })()`);
        await sleep(1000);
        const askShown3 = await evalJs(
          `(() => { const d = document.getElementById("ask");
            return d && !d.classList.contains("hidden"); })()`);
        check("场景C：失败注入下确认框照常弹出", askShown3);
        if (askShown3) {
          await evalJs(`document.getElementById("ask-yes").click(); true`);
          await sleep(1500);
          const cState = await evalJs(`(() => {
            const t = document.getElementById("toast");
            return JSON.stringify({
              toastBad: !!(t && t.classList.contains("show") && t.classList.contains("bad")),
              toastText: t ? t.textContent : "",
              dirStill: !!document.querySelector(${dirSelC}),
              hiddenDirs: JSON.parse(localStorage.getItem("tutti.hiddenDirs") || "[]"),
            });
          })()`);
          const cs = JSON.parse(cState);
          check("场景C：归档失败弹出错误 toast（不静默）",
            cs.toastBad && /归档失败/.test(cs.toastText), cState);
          check("场景C：文件夹留在侧栏（不假成功）", cs.dirStill, cState);
          check("场景C：目录未进隐藏表", !cs.hiddenDirs.includes(archWd), cState);
          await evalJs(`(async () => {
            window.fetch = window.__origFetch;
            const dir = document.querySelector(${dirSelC});
            dir.dispatchEvent(new MouseEvent("contextmenu",
              { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }));
            await new Promise(r => setTimeout(r, 250));
            [...document.querySelectorAll("#ctx-menu .ctx-item")]
              .find(x => x.textContent.trim() === "移除该文件夹").click();
          })()`);
          await sleep(1000);
          const askShown4 = await evalJs(
            `(() => { const d = document.getElementById("ask");
              return d && !d.classList.contains("hidden"); })()`);
          if (!askShown4) { check("场景C：恢复后重试正常隐藏文件夹", false, "确认框未弹出"); }
          else {
            await evalJs(`document.getElementById("ask-yes").click(); true`);
            let gone3 = false, w3 = 0;
            while (w3 < 10000) {
              await sleep(1000); w3 += 1000;
              gone3 = await evalJs(`!document.querySelector(${dirSelC})`);
              if (gone3) break;
            }
            check("场景C：恢复后重试正常隐藏文件夹", gone3, "等了 " + w3 + "ms 仍在");
          }
        }
      }
    }

    const errs = await evalJs(`(window.__errs||[]).join(" | ")`).catch(() => "");
    if (errs) console.log("页面报错: " + errs);
  } finally {
    // taskkill 必须走 spawn 数组：execSync 的 "/PID" 会被 Git Bash 当路径改写，
    // 杀树静默失败留下残留 Edge 抢 CDP 口（实测两棵残留树把下轮跑挂）
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) {}
    // 复杀+验证：Edge 会自己再孵化浏览器进程，树杀后 CDP 口可能仍被占，甚至
    // 杀完隔几秒又冒一个（实测）；按 netstat 监听 PID 补刀（不走 WMI——系统负载
    // 高时 Get-CimInstance 会挂死），连续两轮全空才算干净。
    let cleanRounds = 0;
    for (let round = 0; round < 6 && cleanRounds < 2; round++) {
      await sleep(1200);
      const stale = new Set();
      for (const port of [PORT, CDP_PORT]) {
        try {
          const out = execSync(`netstat -ano | findstr ":${port} " | findstr "LISTENING"`,
            { stdio: "pipe" }).toString();
          for (const m of out.matchAll(/\s(\d+)\s*$/gm)) stale.add(m[1]);
        } catch (e) { /* 该口已空 */ }
      }
      if (!stale.size) { cleanRounds++; continue; }
      cleanRounds = 0;
      for (const p of stale) {
        try { spawn("taskkill", ["/F", "/T", "/PID", p], { stdio: "ignore" }); } catch (e) {}
      }
    }
    await sleep(500);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
  console.log(results.every(Boolean) ? "PROBE_OK" : "PROBE_FAIL");
  process.exit(results.every(Boolean) ? 0 : 1);
}
main();
