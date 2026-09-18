/* 手工添加模型 UI 回归：厂商列表接口调不通时的通路。
 * 服务端口 18941 / CDP 9361（防并行撞车）。
 * 覆盖：models 为 null 的空态出添加行、加模型即时上列表并落 manual 标记、
 * 已有列表的供应商同样可加、重名 toast 报「模型已存在」。 */
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18941;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9361);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uimanualadd-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "models.json"), JSON.stringify({
    providers: [
      // 从未拉到模型列表（models 缺失 = null）：列表接口调不通的典型现场
      { id: "prov-null", name: "列表坏网关", protocol: "openai",
        base_url: "https://broken.test/v1", api_key: "sk-test-aaaaaaaaaaaaaaaa",
        enabled: true },
      // 已有模型列表：添加行应与过滤行共存
      { id: "prov-full", name: "正常网关", protocol: "anthropic",
        base_url: "https://good.test/v1", api_key: "sk-test-bbbbbbbbbbbbbbbb",
        enabled: true, model: "m1",
        models: [{ name: "m1", enabled: true, priority: 1 },
                 { name: "m2", enabled: false, priority: 2 }] },
    ],
    bindings: {},
  }, null, 2), "utf8");

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

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
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

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    check("页面数据就绪", await waitFor(`(S.providers||[]).length >= 2`));

    // 1) models 为 null 的供应商：空态提示 + 手工添加行都在
    await evalJs(`switchTab("models"); "ok"`);
    await sleep(800);
    await evalJs(`selectProvider("prov-null"); "ok"`);
    await sleep(700);
    let boxTxt = await evalJs(`document.querySelector("#prov-detail").textContent`);
    check("空态提示在", boxTxt.includes("尚未获取模型列表"), boxTxt.slice(0, 120));
    check("空态给出「接口调不通」指引", boxTxt.includes("手工添加模型名"), boxTxt.slice(0, 200));
    check("空态有添加输入行", await waitFor(`!!document.getElementById("pm-add-prov-null")`));
    check("空态有「添加」按钮", await waitFor(
      `!!document.querySelector("#prov-detail .pm-add button")`));

    // 2) 填模型名点添加 → 列表即时出现并落 manual 标记
    await evalJs(`document.getElementById("pm-add-prov-null").value = "  deepseek-v32  "; "ok"`);
    await evalJs(`document.querySelector("#prov-detail .pm-add button").click(); "ok"`);
    check("模型上列表", await waitFor(
      `((S.providers||[]).find(p=>p.id==="prov-null").models||[]).some(m=>m.name==="deepseek-v32")`));
    const api1 = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const pm = ((api1.providers || []).find((p) => p.id === "prov-null").models || [])
      .find((m) => m.name === "deepseek-v32");
    check("manual 标记落盘且空白已剥", pm && pm.manual === true && pm.enabled === true,
      JSON.stringify(pm));
    check("输入行随重绘清空/换列表视图", await waitFor(
      `!!document.getElementById("pm-add-prov-null") && document.getElementById("pm-add-prov-null").value === ""`));

    // 3) 已有列表的供应商：添加行在过滤行旁，加完进启用块末尾
    await evalJs(`selectProvider("prov-full"); "ok"`);
    await sleep(700);
    check("已有列表也有添加输入行", await waitFor(`!!document.getElementById("pm-add-prov-full")`));
    check("过滤行共存", await waitFor(`!!document.getElementById("pm-search-prov-full")`));
    // 布局：过滤/计数/添加同一行（y 对齐），添加块靠右、按钮在输入框同排
    const geoRaw = await evalJs(`(() => {
      const r = (sel) => { const e = document.querySelector(sel); if (!e) return null;
        const b = e.getBoundingClientRect();
        return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
      return JSON.stringify({ search: r("#pm-search-prov-full"), cnt: r("#pm-count-prov-full"),
        add: r("#pm-add-prov-full"), btn: r(".pm-tools .pm-add button"),
        fs: [getComputedStyle(document.getElementById("pm-search-prov-full")).fontSize,
             getComputedStyle(document.getElementById("pm-add-prov-full")).fontSize] });
    })()`);
    const geo = JSON.parse(geoRaw || "{}");
    check("过滤与添加输入框字号一致", geo.fs && geo.fs[0] === geo.fs[1], String(geo.fs));
    check("过滤与添加同行等高", geo.search && geo.add &&
      Math.abs(geo.search[1] - geo.add[1]) <= 3 && geo.search[3] === geo.add[3], geoRaw);
    check("添加块靠右（在计数右侧留出空隙）", geo.cnt && geo.add && geo.add[0] > geo.cnt[0] + 40, geoRaw);
    check("添加按钮与输入框同排（垂直居中）", geo.btn && geo.add && geo.btn[0] > geo.add[0] &&
      Math.abs((geo.btn[1] + geo.btn[3] / 2) - (geo.add[1] + geo.add[3] / 2)) <= 3, geoRaw);
    await evalJs(`document.getElementById("pm-add-prov-full").value = "mz"; "ok"`);
    await evalJs(`addModelManual("prov-full"); "ok"`);
    check("第二个供应商模型上列表", await waitFor(
      `((S.providers||[]).find(p=>p.id==="prov-full").models||[]).some(m=>m.name==="mz")`));
    const order = await evalJs(
      `JSON.stringify(((S.providers||[]).find(p=>p.id==="prov-full").models||[])` +
      `.slice().sort((a,b)=>(a.priority||999)-(b.priority||999)).map(m=>m.name))`);
    check("新模型排启用块末尾（不抢 #1）", order === JSON.stringify(["m1", "mz", "m2"]), order);

    // 4) 重名：toast 报「模型已存在」，不产生重复条目
    await evalJs(`document.getElementById("pm-add-prov-full").value = "mz"; "ok"`);
    await evalJs(`addModelManual("prov-full"); "ok"`);
    await sleep(600);
    const toastTxt = await evalJs(`document.getElementById("toast").textContent`);
    check("重名 toast 提示", toastTxt.includes("模型已存在"), toastTxt);
    const cnt = await evalJs(
      `((S.providers||[]).find(p=>p.id==="prov-full").models||[]).filter(m=>m.name==="mz").length`);
    check("无重复条目", cnt === 1, String(cnt));

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
  console.log("\n===== 手工添加模型 UI：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
