/* 编排设置页优化核验：Edge headless + CDP（自带临时服务，端口 18813）。
 * 1) 最大并发任务数为 1-6 分段控件（无数字输入框/单独保存键）；
 * 2) 进入页面后高亮与已存设置一致；
 * 3) 点选 4 → 高亮迁移 + 「已保存：最大并发 4 个任务」；
 * 4) 未选供应商时「测试连通」禁用并带提示。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18813;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9343;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-orch-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
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
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 1) 分段控件结构与旧输入框移除 */
    const seg = await evalJs(`(() => {
      const seg = document.getElementById("set-workers-seg");
      const inp = document.getElementById("set-workers");
      const num = inp && inp.type === "number";
      const saveBtn = [...document.querySelectorAll('#sub-orch button')].find(b => b.getAttribute("onclick") === "saveSettings()");
      return JSON.stringify({ seg: !!seg, n: seg ? seg.querySelectorAll("[data-workers]").length : 0,
        type: inp && inp.type, oldNum: !!num, oldSaveBtn: !!saveBtn });
    })()`);
    const s0 = JSON.parse(seg);
    check("1-6 分段控件存在", s0.seg && s0.n === 6, seg);
    check("旧数字输入框与单独保存键已移除", !s0.oldNum && !s0.oldSaveBtn, seg);

    /* 2) 进编排设置页：高亮与后端设置一致 */
    const st = await evalJs(`(async () => {
      switchTab("orch");
      await new Promise(r => setTimeout(r, 1200));
      const inp = document.getElementById("set-workers");
      const act = document.querySelector("#set-workers-seg .seg-btn.active");
      const provSel = document.getElementById("orch-prov");
      const testBtn = [...document.querySelectorAll('#sub-orch button')].find(b => b.textContent === "测试连通");
      return JSON.stringify({ val: inp && inp.value, active: act && act.dataset.workers,
        testDisabled: testBtn ? testBtn.disabled : null });
    })()`);
    const s1 = JSON.parse(st);
    check("高亮与已保存设置一致（默认 3）", s1.val === "3" && s1.active === "3", st);
    check("未选供应商时测试连通禁用", s1.testDisabled === true, st);

    /* 3) 点选 4：高亮迁移 + 保存成功 */
    const pick = await evalJs(`(async () => {
      const b = document.querySelector('#set-workers-seg [data-workers="4"]');
      b.click();
      await new Promise(r => setTimeout(r, 1200));
      const inp = document.getElementById("set-workers");
      const act = document.querySelector("#set-workers-seg .seg-btn.active");
      const msg = document.getElementById("settings-msg");
      const api = await fetch("/api/settings").then(r => r.json());
      return JSON.stringify({ val: inp.value, active: act && act.dataset.workers,
        msg: msg && msg.textContent, server: api.max_concurrent_jobs });
    })()`);
    const s2 = JSON.parse(pick);
    check("点选 4 后高亮迁移", s2.val === "4" && s2.active === "4", pick);
    check("后端设置已落盘为 4", s2.server === 4, pick);
    check("保存反馈文案正确", /已保存：最大并发 4 个任务/.test(s2.msg || ""), pick);

    const fails = results.filter((r) => !r.ok);
    console.log(fails.length ? "\n✗ " + fails.length + " 项未过" : "\n全部通过");
    process.exitCode = fails.length ? 1 : 0;
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(2); });
