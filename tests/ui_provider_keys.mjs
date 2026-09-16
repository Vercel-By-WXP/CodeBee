/* 多 KEY UI 验证：临时服务（种子：单 KEY 供应商 + 预冷却双 KEY 供应商）→
 * Edge headless + CDP：密钥区渲染 → 应用内弹框添加密钥 → 镜像切换 →
 * 启停/重置/拖拽排序 → 复制供应商 → 截图。端口 18818 / CDP 9348（防并行撞车）。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18818;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9348);
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

const K1 = "sk-test-aaaaaaaaaaaaaaaa";
const K2 = "sk-test-bbbbbbbbbbbbbbbb";
const K3 = "sk-test-cccccccccccccccc";
const COOL = Math.floor(Date.now() / 1000) + 1800;   // 半小时后到期

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uikeys-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "models.json"), JSON.stringify({
    providers: [
      // 老结构：只有 api_key（验证合成一条 + 添加后落成数组）
      { id: "prov-k1", name: "单KEY网关", protocol: "anthropic", base_url: "https://a.test/v1",
        api_key: K1, enabled: true, model: "claude-x",
        models: [{ name: "claude-x", enabled: true, priority: 1 }] },
      // 新结构：两把 KEY，首选欠费冷却中
      { id: "prov-k2", name: "双KEY网关", protocol: "openai", base_url: "https://b.test/v1",
        api_key: K2, enabled: true, model: "gpt-y",
        keys: [
          { id: "k1", key: K2, label: "主号", enabled: true, cool_until: COOL,
            last_error: "HTTP 402 Insufficient Balance" },
          { id: "k2", key: K3, label: "备用号", enabled: true },
        ],
        models: [{ name: "gpt-y", enabled: true, priority: 1 }] },
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
    const shot = (name) => send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", name), Buffer.from(r.result.data, "base64"));
    });

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    check("页面数据就绪", await waitFor(`(S.providers||[]).length >= 2`));

    // 1) 老结构供应商：密钥区按 api_key 合成一条；镜像显示掩码
    await evalJs(`switchTab("models"); "ok"`);
    await sleep(800);
    await evalJs(`selectProvider("prov-k1"); "ok"`);
    await sleep(700);
    let box = await evalJs(`document.querySelector("#prov-detail .keys-box").textContent`);
    check("单 KEY 供应商显示密钥区（#1）", box.includes("#1") && box.includes("添加密钥"), box.slice(0, 120));
    check("密钥掩码显示", box.includes("sk-tes") && !box.includes(K1), box.slice(0, 120));

    // 2) 添加密钥：走应用内 uiPrompt 弹框（两问）
    await evalJs(`promptAddKey("prov-k1"); "ok"`);
    await sleep(500);
    check("弹框出现（新密钥）", await waitFor(`!document.getElementById("ask").classList.contains("hidden")`));
    await evalJs(`document.getElementById("ask-input").value = "${K2}"; "ok"`);
    await evalJs(`document.getElementById("ask-yes").click(); "ok"`);
    await sleep(500);
    check("弹框出现（备注名）", await waitFor(
      `!document.getElementById("ask").classList.contains("hidden") && document.getElementById("ask-input")`));
    await evalJs(`document.getElementById("ask-input").value = "备用号"; "ok"`);
    await evalJs(`document.getElementById("ask-yes").click(); "ok"`);
    const added = await waitFor(
      `((S.providers||[]).find(p=>p.id==="prov-k1").keys||[]).length === 2`);
    check("添加后 keys 数组落成 2 条", added);
    const api1 = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const p1 = (api1.providers || []).find((p) => p.id === "prov-k1");
    check("密钥落盘且掩码回传（真值不外泄）",
      p1.keys.length === 2 && p1.keys[1].key.includes("...") &&
      JSON.stringify(p1).indexOf(K2) < 0,
      JSON.stringify(p1.keys));
    check("供应商标签「2 把密钥」",
      (await evalJs(`document.getElementById("prov-list").textContent`)).includes("2 把密钥"), "");

    // 3) 停用 #1 → 镜像自动切到备用（后端真源验证）
    await evalJs(`keyOp("prov-k1", "k1", "disable"); "ok"`);
    const switched = await waitFor(`!((S.providers||[]).find(p=>p.id==="prov-k1").keys[0].enabled)`);
    const api2 = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const p1b = (api2.providers || []).find((p) => p.id === "prov-k1");
    check("停用 #1 后镜像切到 #2", switched && p1b.api_key === p1b.keys[1].key,
      JSON.stringify(p1b.keys.map((k) => [k.enabled, k.key])));

    // 4) 预冷却供应商：冷却徽标 + 恢复按钮；镜像已自动切到备用
    await evalJs(`selectProvider("prov-k2"); "ok"`);
    await sleep(700);
    let box2 = await evalJs(`document.querySelector("#prov-detail .keys-box").textContent`);
    check("冷却中徽标显示", box2.includes("冷却中"), box2.slice(0, 160));
    check("错误信息显示（402）", box2.includes("402"), box2.slice(0, 160));
    check("使用中标在备用号上", box2.includes("使用中"), box2.slice(0, 160));
    const api3 = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const p2 = (api3.providers || []).find((p) => p.id === "prov-k2");
    // 视图全程掩码：镜像比较只能对掩码值（备用号 = keys[1] 的掩码）
    check("欠费首选被镜像跳过（api_key=备用号）",
      p2.api_key === p2.keys[1].key && p2.api_key.includes("..."), "");
    check("视图里冷却态真实值不外泄", JSON.stringify(api3).indexOf(K2) < 0 && JSON.stringify(api3).indexOf(K3) < 0, "");

    // 5) 恢复（清冷却）→ 首选回到使用中
    await evalJs(`keyOp("prov-k2", "k1", "reset"); "ok"`);
    const recovered = await waitFor(
      `!(S.providers||[]).find(p=>p.id==="prov-k2").keys[0].cool_until > 0 ||
       !(document.querySelector("#prov-detail .keys-box").textContent.includes("冷却中"))`);
    const api4 = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const p2b = (api4.providers || []).find((p) => p.id === "prov-k2");
    check("恢复后镜像切回首选", recovered && p2b.api_key === p2b.keys[0].key,
      JSON.stringify(p2b.keys.map((k) => k.enabled)));
    await shot("keys-detail.png");

    // 6) 拖拽排序：直接调 keyReorder（HTML5 DnD 在 headless 下不稳，事件层已由
    //    bindKeyRowDnD 挂好；这里验证排序动作与落盘）
    await evalJs(`keyReorder("prov-k2", ["k2", "k1"]); "ok"`);
    const reordered = await waitFor(
      `JSON.stringify((S.providers||[]).find(p=>p.id==="prov-k2").keys.map(k=>k.id)) === '["k2","k1"]'`);
    check("排序落盘（备用号提到队首）", reordered);
    const api5 = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const p2c = (api5.providers || []).find((p) => p.id === "prov-k2");
    check("排序后镜像跟随新队首", p2c.api_key === p2c.keys[0].key, "");

    // 7) 复制供应商：出现「副本」且启用
    await evalJs(`duplicateProvider("prov-k1"); "ok"`);
    const dupOk = await waitFor(
      `(S.providers||[]).some(p => p.name.includes("副本"))`);
    const dupApi = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const dup = (dupApi.providers || []).find((p) => p.name.includes("副本"));
    check("复制供应商成功（启用、KEY 重新编号）",
      dupOk && dup && dup.enabled !== false && (dup.keys || []).length === 2 &&
      dup.keys.every((k) => k.id === "k1" || k.id === "k2"),
      JSON.stringify(dup && { name: dup && dup.name, keys: dup && (dup.keys || []).map((k) => k.id) }));
    check("副本不继承冷却状态", dup && !(dup.keys || []).some((k) => k.cooling), "");
    await shot("keys-duplicate.png");

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
  console.log("\n===== 多 KEY UI/CDP：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
