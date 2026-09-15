/* 运行中指挥（详情页消息信箱）端到端验证：Edge headless + CDP，零依赖。
 * 造数用终态 run（启动恢复逻辑会把 running 判为中断残骸，不能造 running），
 * 渲染逻辑用页面内 renderDirector(fakeRun, true) 直驱验证；
 * 发送链路手动指 dirRunId 走 dirSend()，验证：待提交区上传 → 消息入箱 →
 * 附件 commit 进 workdir → state 里可回读。drain 注入语义由单测覆盖。
 * 临时数据目录 + 独立端口，不碰真实 data/ 与 8765。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18820;
const CDP_PORT = 9342;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990101-000000-0001";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-direct-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(runsDir, { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(dataDir, "tasks", "task-d.json"), JSON.stringify({
    id: "task-d", title: "指挥核验任务", type: "novel", goal: "造数：运行中指挥",
    workdir, status: "done", archived: false,
    created_at: "2099-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "指挥核验任务",
    task_id: "task-d", status: "done", steps: [],
    messages: [{ id: "000001", text: "开场说明（已消费）", sender: "种子",
                 attachments: [], created_at: "00:00:01", consumed: true }],
    created_at: "2099-01-01 00:00:00", started_at: "2099-01-01 00:00:00",
    ended_at: "2099-01-01 00:01:00",
    cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18820)", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    // 页面抢控制权（写接口 423 防线）
    const ctrl = await evalJson(`(async () => {
      const d = await api("/api/control", { method: "POST", body: JSON.stringify({ action: "acquire" }) });
      return d && d.ok;
    })()`);
    check("页面获取控制权", ctrl === true);

    // A) 终态任务详情：指挥区自动隐藏（不给已完成任务误导性的输入框）
    const hidden = await evalJson(`(async () => {
      sideOpenTask("task-d");
      await new Promise((r) => setTimeout(r, 700));
      const box = document.getElementById("rd-direct");
      return { boxHidden: box.classList.contains("hidden"),
               hasInput: !!document.getElementById("rd-msg-input") };
    })()`);
    check("终态任务指挥区自动隐藏", hidden.boxHidden === true);
    check("指挥区骨架/控件存在", hidden.hasInput === true);

    // B) UI 渲染直驱：active run + 历史消息 → 区显示、消息流、✓ 已下达样式
    const ui = await evalJson(`(async () => {
      const run = { id: "${RUN_ID}", status: "running", messages: [
        { id: "000001", text: "开场说明（已消费）", sender: "种子", attachments: [],
          created_at: "00:00:01", consumed: true },
        { id: "000002", text: "第三章开头收紧", sender: "测试机",
          attachments: ["_attachments/a.png"], created_at: "00:00:02", consumed: false },
      ] };
      renderDirector(run, true);
      await new Promise((r) => setTimeout(r, 100));
      const box = document.getElementById("rd-direct");
      return {
        visible: !box.classList.contains("hidden"),
        msgs: document.querySelectorAll("#rd-msgs .rd-msg").length,
        consumedTag: !!document.querySelector("#rd-msgs .rd-msg.m-consumed"),
        atts: (document.querySelector("#rd-msgs .rd-msg:last-child .m-atts") || {}).textContent || "",
        inputPh: document.getElementById("rd-msg-input").placeholder,
      };
    })()`);
    check("active 时指挥区显示", ui.visible === true);
    check("消息流渲染 2 条", ui.msgs === 2, ui.msgs);
    check("已消费消息带标记", ui.consumedTag === true);
    check("附件路径展示", (ui.atts || "").includes("_attachments/a.png"), ui.atts);
    check("输入框占位文案就位", (ui.inputPh || "").includes("纠偏"), ui.inputPh);

    // C) 发送链路：手动指 dirRunId（真 run）→ 上传 PNG → dirSend
    const PNG1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";
    const sent = await evalJson(`(async () => {
      const out = {};
      const up = await api("/api/attachments", { method: "POST",
        body: JSON.stringify({ name: "截图.png", data: "${PNG1x1}" }) });
      out.upId = (up.attachment && up.attachment.id) || "";
      dirRunId = "${RUN_ID}";
      dirAtts.push(up.attachment);
      drawDirAtts();
      out.chips = document.querySelectorAll("#rd-attach-list .att-chip2").length;
      document.getElementById("rd-msg-input").value = "第三章别水字数";
      await dirSend();
      out.postOk = out.sent = true;
      out.chipsAfter = document.querySelectorAll("#rd-attach-list .att-chip2").length;
      out.inputCleared = document.getElementById("rd-msg-input").value === "";
      return out;
    })()`);
    check("附件上传拿到 16 位 id", String(sent.upId).length === 16, sent.upId);
    check("待发送附件 chip 显示", sent.chips === 1, sent.chips);
    check("发送后附件 chips 清空", sent.chipsAfter === 0, sent.chipsAfter);
    check("发送后输入框清空", sent.inputCleared === true);

    // D) 后端落盘：信箱 + 附件 commit 进 workdir + state 可回读
    await sleep(400);
    const disk = JSON.parse(readFileSync(join(runsDir, "run.json"), "utf-8"));
    const last = (disk.messages || []).slice(-1)[0] || {};
    check("run.json 信箱落盘（2 条）", (disk.messages || []).length === 2, (disk.messages || []).length);
    check("消息带附件相对路径", (last.attachments || [])[0] === "_attachments/截图.png",
      JSON.stringify(last.attachments));
    check("消息未消费（等 drain）", last.consumed === false);
    check("附件文件 commit 进工作目录",
      existsSync(join(workdir, "_attachments", "截图.png")));
    const st = await (await fetch(`${SERVICE}/api/state`)).json();
    const stRun = (st.runs || []).find((r) => r.id === RUN_ID);
    check("state 携带消息（SSE 回流数据源）", stRun && (stRun.messages || []).length === 2,
      stRun && JSON.stringify((stRun.messages || []).length));

    // E) SSE/重画联动：模拟下一条消息到达后的渲染
    const ui2 = await evalJson(`(async () => {
      if (typeof renderDirector !== "function") return { __err: "renderDirector 未定义（app.js 加载失败？）" };
      const run = { id: "${RUN_ID}", status: "running", messages: [
        { id: "000001", text: "开场说明（已消费）", sender: "种子", attachments: [], created_at: "00:00:01", consumed: true },
        { id: "000002", text: "第三章别水字数", sender: "本机", attachments: ["_attachments/截图.png"], created_at: "00:00:03", consumed: false },
      ] };
      renderDirector(run, true);
      await new Promise((r) => setTimeout(r, 100));
      return {
        msgs: document.querySelectorAll("#rd-msgs .rd-msg").length,
        last: (document.querySelector("#rd-msgs .rd-msg:last-child") || {}).textContent || "",
        hint: document.getElementById("rd-direct-hint").textContent,
      };
    })()`);
    if (ui2 && ui2.__err) console.log("  E 段异常:", ui2.__err);
    check("重画后消息流 2 条", ui2.msgs === 2, ui2.msgs);
    check("新指令内容渲染", (ui2.last || "").includes("第三章别水字数"), ui2.last);
    check("未消费提示语出现", (ui2.hint || "").includes("下一个步骤"), ui2.hint);

    // F) 暂停态：paused run → 提示语切换 + 按钮文案「继续执行」
    const ui3 = await evalJson(`(async () => {
      renderDirector({ id: "${RUN_ID}", status: "running", paused: true, messages: [] }, true);
      const bp = document.getElementById("btn-pause");
      const paused = document.getElementById("rd-direct-hint").textContent;
      renderDirector({ id: "${RUN_ID}", status: "running", messages: [] }, true);
      const resumed = document.getElementById("rd-direct-hint").textContent;
      return { paused, resumed };
    })()`);
    check("暂停提示语切换", (ui3.paused || "").includes("放行"), ui3.paused);
    check("放行后提示语恢复", (ui3.resumed || "") === "", ui3.resumed);

    // F2) 送达回执：consumed_by 消息渲染「已随步骤送达：#NN role」
    const ui4 = await evalJson(`(async () => {
      renderDirector({ id: "${RUN_ID}", status: "running", messages: [
        { id: "000009", text: "带回执的指令", sender: "本机", attachments: [],
          created_at: "00:00:09", consumed: true,
          consumed_by: { step: 4, role: "critique-c2" } },
      ] }, true);
      await new Promise((r) => setTimeout(r, 100));
      return (document.querySelector("#rd-msgs .rd-msg:last-child .m-atts:last-child") || {}).textContent || "";
    })()`);
    check("送达去向显示（#4 critique-c2）", (ui4 || "").includes("#4") && (ui4 || "").includes("critique-c2"), ui4);

    // G) pause 端点：标志位落 run.json + state 可见
    const pz = await evalJson(`(async () => {
      const a = await api("/api/runs/${RUN_ID}/pause", { method: "POST", body: JSON.stringify({ paused: true }) });
      return a && a.ok && a.paused === true;
    })()`);
    check("pause 端点置位", pz === true);
    await sleep(300);
    const disk3 = JSON.parse(readFileSync(join(runsDir, "run.json"), "utf-8"));
    check("paused 标志落盘", disk3.paused === true, disk3.paused);
    const pz2 = await evalJson(`(async () => {
      const a = await api("/api/runs/${RUN_ID}/pause", { method: "POST", body: JSON.stringify({ paused: false }) });
      return a && a.paused === false;
    })()`);
    check("放行端点复位", pz2 === true);

    check("无未捕获 JS 异常", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 200));
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(600);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\nFAILED ${bad}/${results.length}` : `\nOK ${results.length}/${results.length}`);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
