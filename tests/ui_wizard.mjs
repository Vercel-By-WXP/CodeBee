/* 采访式向导（新建任务）核验（Edge headless + CDP，临时端口 18837）：
 * 入口按钮 → 目标（空值拦截）→ 类型卡片选定 → 目录（可留空）→ 确认摘要
 * → 填入表单：goal/type/workdir 预填、类型联动生效、弹框关闭。
 * 另验英文模式下一步按钮词条。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18837;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9359;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-wizard-"));
  const dataDir = join(tmp, "data");
  const workdir = join(tmp, "wd-wizard");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(workdir, { recursive: true });

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    const waitDom = async (expr, tries = 20) => {
      for (let i = 0; i < tries; i++) {
        if (await evalJs(expr)) return true;
        await sleep(300);
      }
      return false;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    const GOAL = "向导造数：把登录页改成暗色主题";
    const wizState = `JSON.stringify((() => ({
      modalHidden: document.getElementById("modal").classList.contains("hidden"),
      goal: (document.getElementById("wz-goal") || {}).value,
      flows: document.querySelectorAll(".wz-flow").length,
      verify: !!document.getElementById("wz-verify"),
      workdir: !!document.getElementById("wz-workdir"),
      sum: (document.querySelector(".wz-sum") || {}).textContent || "",
      title: document.getElementById("modal-title").textContent }))())`;

    check("向导入口按钮可见", await evalJs(
      `(() => { const b = document.getElementById("btn-wizard");
        return !!b && !b.classList.contains("hidden") && b.textContent.includes("向导"); })()`));
    let st;
    await evalJs(`document.getElementById("btn-wizard").click()`);
    // openTaskWizard 现在是 async（冷启动会现拉流程清单）：等弹框就绪而非死等固定时长
    check("第 1 步：目标输入框出现",
      await waitDom(`!document.getElementById("modal").classList.contains("hidden")
        && !!document.getElementById("wz-goal")`));
    st = JSON.parse(await evalJs(wizState));
    check("第 1 步标题 1/N", st.title.includes("1/"), JSON.stringify(st));

    // 空目标拦截：点下一步仍停在第一步
    await evalJs(`document.querySelector("#modal-foot .primary").click()`);
    await sleep(300);
    st = JSON.parse(await evalJs(wizState));
    check("空目标被拦截（仍在第 1 步）", !st.modalHidden && st.title.includes("1/"), JSON.stringify(st));

    await evalJs(`(function(){ const g = document.getElementById("wz-goal");
      g.value = ${JSON.stringify(GOAL)}; g.dispatchEvent(new Event("input", { bubbles: true })); })()`);
    await evalJs(`document.querySelector("#modal-foot .primary").click()`);
    await sleep(300);
    st = JSON.parse(await evalJs(wizState));
    check("第 2 步：类型卡片列表（至少 1 张）", st.flows >= 1 && st.title.includes("2/"), JSON.stringify(st));

    // 第一张卡是 code 流程 → 追问验证命令（5 步）
    const pickedName = await evalJs(
      `(() => { const c = document.querySelector(".wz-flow .wz-fname");
        const name = c ? c.textContent : ""; c.closest(".wz-flow").click(); return name; })()`);
    await sleep(300);
    st = JSON.parse(await evalJs(wizState));
    check("code 流程追问验证命令（第 3/5 步）",
      st.title.includes("3/5") && !st.modalHidden && st.verify, JSON.stringify(st));

    await evalJs(`(function(){ const v = document.getElementById("wz-verify");
      v.value = "npm test"; v.dispatchEvent(new Event("input", { bubbles: true })); })()`);
    await evalJs(`document.querySelector("#modal-foot .primary").click()`);
    await sleep(300);
    st = JSON.parse(await evalJs(wizState));
    check("第 4 步：目录（验证命令答完才到）", st.title.includes("4/5"), JSON.stringify(st));

    await evalJs(`(function(){ const w = document.getElementById("wz-workdir");
      w.value = ${JSON.stringify(workdir)}; w.dispatchEvent(new Event("input", { bubbles: true })); })()`);
    await evalJs(`document.querySelector("#modal-foot .primary").click()`);
    await sleep(300);
    st = JSON.parse(await evalJs(wizState));
    check("第 5 步：确认摘要含目标/验证命令/目录",
      st.title.includes("5/5") && st.sum.includes(GOAL) && st.sum.includes("npm test")
        && st.sum.includes("wd-wizard"), JSON.stringify(st));

    await evalJs(`document.querySelector("#modal-foot .primary").click()`);
    await sleep(500);
    const pf = JSON.parse(await evalJs(`JSON.stringify({
      modalHidden: document.getElementById("modal").classList.contains("hidden"),
      goal: document.getElementById("f-goal").value,
      typeBtn: document.getElementById("f-type-name").textContent,
      verify: document.getElementById("f-verify").value,
      workdir: document.getElementById("f-workdir").value })`));
    check("填入表单：目标/类型/验证命令/目录全部预填",
      pf.modalHidden && pf.goal === GOAL && pf.typeBtn === pickedName
        && pf.verify === "npm test" && pf.workdir === workdir, JSON.stringify({ ...pf, pickedName }));

    // review 流程不追问验证命令：最后一张卡走一遍（4 步直达目录）
    await evalJs(`document.getElementById("btn-wizard").click()`);
    await sleep(400);
    await evalJs(`(function(){ const g = document.getElementById("wz-goal");
      g.value = "向导造数：评审流程"; g.dispatchEvent(new Event("input", { bubbles: true })); })()`);
    await evalJs(`document.querySelector("#modal-foot .primary").click()`);
    await sleep(300);
    await evalJs(`(function(){ const cards = document.querySelectorAll(".wz-flow");
      cards[cards.length - 1].click(); })()`);
    await sleep(300);
    st = JSON.parse(await evalJs(wizState));
    check("review 流程无验证步（直入目录 3/4）",
      st.title.includes("3/4") && !st.verify && st.workdir, JSON.stringify(st));
    await evalJs(`closeModal()`);
    await sleep(300);

    // 英文模式：向导词条走 t()
    await evalJs(`localStorage.setItem("orch.lang", "en"); location.reload()`);
    await sleep(3500);
    await evalJs(`document.getElementById("btn-wizard").click()`);
    await sleep(400);
    const en = JSON.parse(await evalJs(`JSON.stringify({
      next: (document.querySelector("#modal-foot .primary") || {}).textContent || "",
      ph: (document.getElementById("wz-goal") || {}).placeholder || "" })`));
    check("英文模式：向导词条生效（Next / placeholder 英文）",
      en.next === "Next" && /one sentence|Goal|what/i.test(en.ph), JSON.stringify(en));
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { if (svc) svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
