/* 数据与备份页 UI 验证：临时服务（种子：旧运行日志/bak/pending 垃圾 + 1 任务工作目录）→
 * Edge headless + CDP：页签进入 → 清理预估渲染 → 立即清理（记录保留/垃圾消失）→
 * 导出（zip 落盘）→ 导入预览 → 确认导入（合并）→ 重启提示 + 反悔备份。
 * 端口 18933 / CDP 9362（防并行撞车）。settings.json 种 cleanup_enabled=false，
 * 防服务启动后 automation tick 抢先把种好的垃圾清掉。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync, existsSync, readFileSync, readdirSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18933;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9362);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => existsSync(p));

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uidata-"));
  const dataDir = join(tmp, "data");
  const wsRoot = join(tmp, "ws");
  const outDir = join(tmp, "out");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", "r-20200101-000000-0001", "steps"), { recursive: true });
  mkdirSync(join(dataDir, "pending", "abc"), { recursive: true });
  mkdirSync(join(wsRoot, "novel1"), { recursive: true });
  mkdirSync(outDir, { recursive: true });

  // 种垃圾：过期运行过程日志 + bak 残留 + 过期信箱（mtime 拨到 2020）
  writeFileSync(join(dataDir, "runs", "r-20200101-000000-0001", "run.json"),
    JSON.stringify({ id: "r-20200101-000000-0001", status: "done",
      ended_at: "2020-01-01 00:00:00", title: "旧运行", steps: [] }), "utf8");
  writeFileSync(join(dataDir, "runs", "r-20200101-000000-0001", "steps", "01-draft.log"),
    "x".repeat(4096), "utf8");
  writeFileSync(join(dataDir, "models.json.bak-old"), "{}", "utf8");
  writeFileSync(join(dataDir, "pending", "abc.json"), "{}", "utf8");
  const old = new Date("2020-01-01T00:00:00");
  for (const p of [join(dataDir, "runs", "r-20200101-000000-0001", "run.json"),
                   join(dataDir, "runs", "r-20200101-000000-0001", "steps", "01-draft.log"),
                   join(dataDir, "models.json.bak-old"),
                   join(dataDir, "pending", "abc.json"), join(dataDir, "pending", "abc")]) {
    utimesSync(p, old, old);
  }
  // 种一个任务 + 工作目录文件（导出应带上 workspace/novel1）
  writeFileSync(join(dataDir, "tasks", "t-20200101-000000-0001.json"), JSON.stringify({
    id: "t-20200101-000000-0001", type: "doc", title: "种子任务", goal: "写个开篇",
    workdir: join(wsRoot, "novel1"), status: "done",
    created_at: "2020-01-01 00:00:00", attachments: [],
  }), "utf8");
  writeFileSync(join(wsRoot, "novel1", "ch01.md"), "第一章 蜜蜂出发。", "utf8");
  // 清理默认关：防 automation tick 抢先把垃圾清掉，测试里走手动「立即清理」
  writeFileSync(join(dataDir, "settings.json"), JSON.stringify({
    cleanup_enabled: false, default_workdir: wsRoot }), "utf8");

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    cwd: ROOT, stdio: "ignore",
    env: Object.assign({}, process.env, { TUTTI_DATA: dataDir, PYTHONPATH: ROOT }),
  });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE, [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    if (!target) throw new Error("no CDP target");

    ws = new WebSocket(target.webSocketDebuggerUrl);
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
    const waitFor = async (expr, ms = 15000) => {
      for (let t = 0; t < ms; t += 400) {
        if (await evalJs(expr)) return true;
        await sleep(400);
      }
      return false;
    };
    const shot = (name) => send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", name), Buffer.from(r.result.data, "base64"));
    });

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    check("页面数据就绪（种子任务在侧栏可见）",
      await waitFor(`document.body.textContent.includes("种子任务")`));

    // 1) 进入「数据与备份」页：导航 + 页签渲染 + 清理预估
    await evalJs(`switchTab("data"); "ok"`);
    await sleep(600);
    check("子页可见且标题正确",
      (await evalJs(`!document.getElementById("sub-data").classList.contains("hidden")
        && document.getElementById("page-title").textContent`)) === "数据与备份");
    check("清理预估列出运行过程日志",
      await waitFor(`document.querySelectorAll("#cl-plan .cl-row").length >= 1
        && document.getElementById("cl-plan").textContent.includes("运行过程日志")`));
    check("状态行显示尚未清理", await waitFor(
      `document.getElementById("cl-state").textContent.includes("尚未清理过")`));

    // 2) 立即清理：垃圾消失、记录与报告保留、状态行更新
    await evalJs(`runCleanup(false); "ok"`);
    check("清理完成状态行更新", await waitFor(
      `document.getElementById("cl-state").textContent.includes("上次清理")`));
    await sleep(400);
    check("steps 过程日志已删", !existsSync(join(dataDir, "runs", "r-20200101-000000-0001", "steps")));
    check("run.json 运行记录保留", existsSync(join(dataDir, "runs", "r-20200101-000000-0001", "run.json")));
    check("bak 残留已清", !existsSync(join(dataDir, "models.json.bak-old")));
    check("过期信箱已清", !existsSync(join(dataDir, "pending", "abc.json")));
    const stApi = await fetch(SERVICE + "/api/cleanup").then((r) => r.json());
    check("API 状态落盘（last_freed>0）", (stApi.state?.last_freed || 0) > 0, JSON.stringify(stApi.state || {}));

    // 3) 导出：指定输出目录 → zip 落盘 + 结果行回显
    await evalJs(`document.getElementById("bk-target").value = ${JSON.stringify(outDir)}; "ok"`);
    await evalJs(`exportBackup(); "ok"`);
    check("导出结果回显", await waitFor(
      `document.getElementById("bk-result").textContent.includes("已导出")`, 60000));
    const zips = readdirSync(outDir).filter((n) => n.startsWith("codebee-backup-") && n.endsWith(".zip"));
    check("备份 zip 已落盘（含 1 任务）", zips.length === 1
      && (await evalJs(`document.getElementById("bk-result").textContent`)).includes("1"),
      zips.join(","));
    const zipPath = zips.length ? join(outDir, zips[0]) : "";

    // 4) 导入预览：同机导出即同机导入（无需重映射）
    await evalJs(`document.getElementById("bi-path").value = ${JSON.stringify(zipPath)}; "ok"`);
    await evalJs(`inspectBackup(); "ok"`);
    check("预览显示备份时间与任务数", await waitFor(
      `document.getElementById("bi-preview").textContent.includes("备份时间")
       && document.getElementById("bi-preview").textContent.includes("任务")`));
    check("确认导入按钮出现", await waitFor(
      `!document.getElementById("bi-apply").classList.contains("hidden")`));

    // 5) 确认导入（合并）：应用内弹框确认 → 完成回显 + 重启按钮
    await evalJs(`applyBackupImport(); "ok"`);
    await sleep(400);
    check("确认弹框出现", await waitFor(
      `!document.getElementById("ask").classList.contains("hidden")`));
    await evalJs(`document.getElementById("ask-yes").click(); "ok"`);
    check("合并导入完成回显", await waitFor(
      `document.getElementById("bi-preview").textContent.includes("合并导入完成")`, 60000));
    check("重启服务按钮出现", await waitFor(
      `!document.getElementById("bi-restart").classList.contains("hidden")`));
    const preImports = readdirSync(join(dataDir, "imports")).filter((n) => n.startsWith("pre-import-"));
    check("导入前反悔备份已生成", preImports.length === 1, preImports.join(","));
    check("种子任务仍在（合并覆盖同 id）",
      existsSync(join(dataDir, "tasks", "t-20200101-000000-0001.json")));
    await shot("data-backup.png");

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) {
      if (p && p.pid) {
        try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
      }
    }
    if (results.some((r) => !r.ok)) {
      console.log("（失败现场保留：%s）", tmp);
    } else {
      try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 数据与备份 UI/CDP：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
