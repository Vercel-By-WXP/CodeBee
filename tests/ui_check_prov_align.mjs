/* 厂商列表卡片「顶端对齐」验收：
 *   每张厂商卡 = 名称行（名称 + 右侧「N 模型」计数，顶端对齐）+ 第二行协议标签；
 *   所有卡的计数同一垂直位置、与名称首行对齐，无横向溢出。
 * 自含临时服务（端口 18796），只读操作不抢设备控制权。
 * 用法：node tests/ui_check_prov_align.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18796;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9350;
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
  const tmp = mkdtempSync(join(tmpdir(), "tutti-prov-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "models.json"), JSON.stringify({
    providers: [
      { id: "prov-a", name: "维云模型 YBJ", protocol: "openai",
        base_url: "https://a.test/v1", api_key: "sk-" + "a".repeat(20),
        models: Array.from({ length: 71 }, (_, i) => ({ name: "m-a-" + i, priority: i + 1 })) },
      { id: "prov-b", name: "Z.ai · API Key", protocol: "anthropic",
        base_url: "https://b.test/v1", api_key: "sk-" + "b".repeat(20),
        models: Array.from({ length: 10 }, (_, i) => ({ name: "m-b-" + i, priority: i + 1 })) },
      { id: "prov-c", name: "cavoti", protocol: "anthropic",
        base_url: "https://c.test/v1", api_key: "sk-" + "c".repeat(20), enabled: false,
        models: Array.from({ length: 3 }, (_, i) => ({ name: "m-c-" + i, priority: i + 1 })) },
    ],
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
    check("Edge headless 启动并开放 CDP", !!target);
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
    for (let i = 0; i < 20; i++) {
      const n = await js(`document.querySelectorAll("#prov-list .prov-item").length`);
      if (n > 0) break;
      await sleep(500);
    }

    const out = JSON.parse(await js(`JSON.stringify((() => {
      const items = [...document.querySelectorAll("#prov-list .prov-item")];
      const geom = items.map((it) => {
        const name = it.querySelector(".pi-name");
        const n = it.querySelector(".pi-n");
        const meta = it.querySelector(".pi-meta");
        const nr = name.getBoundingClientRect(), tr = n.getBoundingClientRect();
        const mr = meta.getBoundingClientRect();
        return { nameTop: +nr.top.toFixed(1), nTop: +tr.top.toFixed(1),
          nRight: +tr.right.toFixed(1), metaTop: +mr.top.toFixed(1),
          nameBottom: +nr.bottom.toFixed(1), text: n.textContent.trim() };
      });
      return {
        count: items.length,
        geom,
        nTopsUniform: new Set(geom.map((g) => g.nTop)).size === 1,
        topAligned: geom.every((g) => Math.abs(g.nTop - g.nameTop) <= 3),
        metaBelowName: geom.every((g) => g.metaTop >= g.nameBottom - 1),
        overflow: (() => { const t = document.getElementById("prov-list");
          return t ? t.scrollWidth - t.clientWidth : -1; })(),
        cols: (() => { const s = document.querySelector(".prov-side");
          const d = document.querySelector(".prov-detail");
          return s && d ? { side: +s.getBoundingClientRect().top.toFixed(1),
            detail: +d.getBoundingClientRect().top.toFixed(1) } : null; })(),
      };
    })())`));
    check("渲染出 3 张厂商卡", out.count === 3, JSON.stringify(out.count));
    check("每张卡：计数与名称首行顶端对齐", out.topAligned, JSON.stringify(out.geom));
    check("所有卡的计数同一垂直位置", out.nTopsUniform, JSON.stringify(out.geom.map((g) => g.nTop)));
    check("协议标签行在名称行下方", out.metaBelowName, JSON.stringify(out.geom));
    check("计数文本正确（71/10/3 模型）",
      JSON.stringify(out.geom.map((g) => g.text)) === JSON.stringify(["71 模型", "10 模型", "3 模型"]),
      JSON.stringify(out.geom.map((g) => g.text)));
    check("厂商列表无横向溢出", out.overflow <= 0, "overflow=" + out.overflow);
    check("左栏供应商框与右列详情顶端对齐", out.cols && Math.abs(out.cols.side - out.cols.detail) <= 3 &&
      out.cols.side <= out.cols.detail + 1, JSON.stringify(out.cols));

    const shot = await send("Page.captureScreenshot", { format: "png" });
    if (shot?.result?.data) {
      const fs = await import("node:fs");
      fs.mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      fs.writeFileSync(join(ROOT, ".ui-shots", "prov-align.png"), Buffer.from(shot.result.data, "base64"));
    }
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { if (svc) spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* 下次清理 */ }
  }

  const bad = FAIL.length;
  console.log("\n===== 厂商列表对齐核验：%d 通过 / %d 失败 =====", PASS.length, bad);
  if (bad) process.exit(1);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
