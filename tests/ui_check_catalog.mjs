/* 智能体目录卡片样式验收：
 * 1) 「保存」按钮单行横排（宽度 ≥ 高度的 1.6 倍，不被挤成竖排字）
 * 2) select 与按钮同行且不重叠
 * 3) 「卸载」在卡片头行（与名称同一行顶端），不再吊在操作行尾
 * 4) 操作行内按钮互不重叠、卡片无横向溢出
 * 自含临时服务（端口 18796），只读操作不抢设备控制权。
 * 用法：node tests/ui_check_catalog.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18796;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9349;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-cat-"));
  // 种一个带模型列表的供应商：没有可选模型时卡片走「还没有可选模型」提示分支，
  // 出现不了「下拉 + 保存」布局，无法验收
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "models.json"), JSON.stringify({
    providers: [{ id: "prov-test", name: "测试厂商", protocol: "openai",
      base_url: "https://t.test/v1", api_key: "sk-test-" + "x".repeat(20),
      models: [{ name: "gpt-test-a", priority: 1 }, { name: "gpt-test-b", priority: 2 }] }],
    bindings: {},
  }));
  let edge = null, ws = null, svc = null;
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
    check("临时服务启动（端口 " + PORT + "）", up);

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
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await js(`switchTab("agents"); "ok"`);
    await sleep(2500);

    const dump = await js(`(() => {
      const cards = [...document.querySelectorAll("#ag-installed .card")];
      if (!cards.length) return { err: "已安装区无卡片" };
      const out = { cardCount: cards.length, saves: [], uninstalls: [], badOps: [] };
      for (const card of cards) {
        // 保存按钮：在 modelBox 的 input-row 里
        const row = card.querySelector(".input-row");
        const btn = row ? row.querySelector("button") : null;
        const sel = row ? row.querySelector("select") : null;
        if (btn && sel) {
          const b = btn.getBoundingClientRect(), s = sel.getBoundingClientRect();
          out.saves.push({ w: Math.round(b.width), h: Math.round(b.height),
            text: btn.textContent.trim(),
            sameRow: Math.abs((s.top + s.height / 2) - (b.top + b.height / 2)) < 6,
            noOverlap: s.right <= b.left + 1 });
        }
        // 保存按钮文字行数（竖排折行时 >1）：单独一趟量，Range 取文字矩形
        out.saveLines = [...document.querySelectorAll("#ag-installed .card .input-row button")]
          .map((btn) => {
            const r = document.createRange();
            r.selectNodeContents(btn);
            return { text: btn.textContent.trim(),
              lines: [...r.getClientRects()].filter(x => x.width > 2).length };
          });
        // 卸载按钮：应在 .head 里，与卡片名同一视觉行
        const un = [...card.querySelectorAll(":scope > .head button.danger")].find(b => b.textContent.includes("卸载"));
        if (un) {
          const name = card.querySelector(".head .name");
          const b = un.getBoundingClientRect(), n = name.getBoundingClientRect();
          out.uninstalls.push({ inHead: true, sameRow: Math.abs(n.top - b.top) < 6 });
          const dangling = [...card.querySelectorAll(".ops button")].find(x => x.textContent.includes("卸载"));
          out.uninstalls[out.uninstalls.length - 1].notInOps = !dangling;
        }
        // 操作行按钮两两不重叠
        const ops = [...card.querySelectorAll(".ops button")].map(x => x.getBoundingClientRect());
        for (let i = 1; i < ops.length; i++)
          if (ops[i - 1].right > ops[i].left + 1) out.badOps.push(card.querySelector(".name").textContent);
      }
      return out;
    })()`);
    check("目录：已安装卡片已渲染", !dump.err && (dump.cardCount || 0) > 0, JSON.stringify(dump).slice(0, 150));

    const saves = dump.saves || [];
    check("目录：存在「默认模型」下拉 + 保存按钮的卡片", saves.length > 0, "saves=" + saves.length);
    const lines = dump.saveLines || [];
    check("目录：保存按钮文字单行（Range 行数=1，非竖排）",
      lines.length > 0 && lines.every((l) => l.text === "保存" && l.lines === 1), JSON.stringify(lines));
    check("目录：保存按钮高度正常（拉伸对齐下拉但 ≤48px）",
      saves.every((s) => s.h <= 48), JSON.stringify(saves));
    check("目录：select 与保存按钮同一行", saves.every((s) => s.sameRow), JSON.stringify(saves));
    check("目录：select 与保存按钮不重叠", saves.every((s) => s.noOverlap), JSON.stringify(saves));

    const uns = dump.uninstalls || [];
    check("目录：卸载按钮在卡片头行", uns.length > 0 && uns.every((u) => u.inHead && u.sameRow), JSON.stringify(uns));
    check("目录：操作行不再有卸载", uns.every((u) => u.notInOps), JSON.stringify(uns));
    check("目录：操作行按钮无重叠", (dump.badOps || []).length === 0, JSON.stringify(dump.badOps));

    // 手机窄屏：卡片不横向溢出，保存按钮仍是横排
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await sleep(900);
    const mobOver = await js(`(() => { const d = document.documentElement;
      return d.scrollWidth > d.clientWidth + 2 ? (d.scrollWidth + ">" + d.clientWidth) : ""; })()`);
    check("目录：手机窄屏无横向溢出", mobOver === "", mobOver);
    const mobSave = await js(`(() => {
      const btn = document.querySelector("#ag-installed .card .input-row button");
      if (!btn) return null;
      const r = document.createRange();
      r.selectNodeContents(btn);
      return { text: btn.textContent.trim(),
        lines: [...r.getClientRects()].filter(x => x.width > 2).length };
    })()`);
    check("目录：手机窄屏保存按钮仍单行", mobSave && mobSave.text === "保存" && mobSave.lines === 1,
      JSON.stringify(mobSave));
    await send("Emulation.clearDeviceMetricsOverride");

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    if (svc?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  console.log("\n===== 目录卡片样式验收：%d 通过 / %d 失败 =====", PASS.length, FAIL.length);
  if (FAIL.length) { console.log("失败项：", FAIL); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
