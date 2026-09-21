/* 一次性探针：对话条三件套（mode/thinking/model pill）回填与改值落库。
 * 起临时服务 + 种一个已完成的 direct 任务 → 打开对话页 → 验证回填 →
 * 改 mode/thinking → 断言任务参数已写（/api/tasks/<id>）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18950 + (process.pid % 100);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9820 + (process.pid % 100);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, cond, detail = "") => {
  results.push(cond);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
};

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-chatprefs-"));
  const workdir = join(dataDir, "work");
  mkdirSync(workdir, { recursive: true });
  // 种一个已完成的 direct 任务（不真跑：服务启动前落盘，建任务会真跑 run）
  const taskId = "t_probechat1";
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "direct", engine: "direct", title: "chatprefs", goal: "探针",
    context: "", workdir, mode: "expert", thinking: "high", implementer: "",
    attachments: [], created_at: "2026-09-21 12:00:00", status: "done",
    direct_provider_id: "", direct_model: "",
  }), "utf8");
  const svc = spawn("python", ["app/main.py", "--port", String(PORT), "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) { await sleep(500); try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) {} }
  check("临时服务就绪", up);
  const st0 = await (await fetch(SERVICE + "/api/state")).json();
  check("种的任务被加载", (st0.tasks || []).some((x) => x.id === taskId));

  const profile = mkdtempSync(join(tmpdir(), "tutti-chatprefs-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"],
    { stdio: "ignore" });
  try {
    let wsUrl = null;
    for (let i = 0; i < 30 && !wsUrl; i++) {
      await sleep(400);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json`)).json();
        wsUrl = list.find((t) => t.type === "page")?.webSocketDebuggerUrl;
      } catch (e) {}
    }
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval failed");
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await evalJs(`(function () { if (window.welcomeClose) welcomeClose(); return 1; })()`);

    // 直接调渲染链：模拟打开该任务详情（把 S.lastRun 置为伪 run 让 rdPrefsPush 可寻任务）
    const filled = await evalJs(`(async () => {
      const st = await api("/api/state");
      const task = (st.tasks || []).find((x) => x.id === ${JSON.stringify(taskId)});
      if (!task) return "no-task";
      S.state = st;
      S.lastRun = { id: "r-probe", task_id: task.id, status: "done" };
      rdPrefsFill(task);
      return JSON.stringify({ mode: document.getElementById("rd-mode").value,
        think: document.getElementById("rd-thinking").value,
        modeFace: document.getElementById("rd-mode-face").textContent,
        thinkFace: document.getElementById("rd-thinking-face").textContent,
        modelBtn: document.getElementById("rd-model-btn").textContent });
    })()`);
    const f = JSON.parse(filled);
    check("回填模式 expert + 短名「专家」", f.mode === "expert" && f.modeFace === "专家", filled);
    check("回填思考 high + 短名「深度」", f.think === "high" && f.thinkFace === "深度", filled);
    check("模型 pill 默认「自动推荐」", f.modelBtn === "自动推荐", f.modelBtn);

    // 改 mode → 落库
    await evalJs(`(function () {
      const s = document.getElementById("rd-mode");
      s.value = "fast"; s.dispatchEvent(new Event("change", { bubbles: true }));
      const s2 = document.getElementById("rd-thinking");
      s2.value = "low"; s2.dispatchEvent(new Event("change", { bubbles: true }));
      return 1; })()`);
    await sleep(800);
    const task2 = await (await fetch(SERVICE + "/api/state")).json();
    const t2 = (task2.tasks || []).find((x) => x.id === taskId);
    check("改后任务 mode=fast 落库", t2 && t2.mode === "fast", t2 && t2.mode);
    check("改后任务 thinking=low 落库", t2 && t2.thinking === "low", t2 && t2.thinking);
    const face2 = await evalJs(`JSON.stringify({
      m: document.getElementById("rd-mode-face").textContent,
      t: document.getElementById("rd-thinking-face").textContent })`);
    const ff = JSON.parse(face2);
    check("face 跟随「快速/快速」", ff.m === "快速" && ff.t === "快速", face2);
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const fails = results.filter((x) => !x).length;
  console.log(fails ? `\n${fails}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
