/* wire 协议适配 UI 验证：自起临时服务（种子：openai 供应商指向本地假网关）
 * + 本地假网关（/v1/messages 等返回 200）→ 模型接入页点「适配测试」→
 * 断言适配徽标落上 → CLI 绑定页断言链芯片从 ⚠ 变「✓ 已适配」→ 截图。
 * 结束清理浏览器/网关/服务进程、临时目录。 */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18817;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9347);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* 本地假网关：messages / chat/completions / responses 一律 200（探测即通过） */
function startGateway() {
  return new Promise((res) => {
    const srv = createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        const ok = req.url.endsWith("/messages") ||
          req.url.endsWith("/chat/completions") || req.url.endsWith("/responses");
        res.writeHead(ok ? 200 : 404, { "Content-Type": "application/json" });
        res.end(ok ? '{"content":[],"choices":[]}' : '{"error":"no route"}');
      });
    });
    srv.listen(0, "127.0.0.1", () => res(srv));
  });
}

async function main() {
  const gw = await startGateway();
  const gwPort = gw.address().port;
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uiwire-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "models.json"), JSON.stringify({
    providers: [
      { id: "prov-wa", name: "网关A", protocol: "anthropic", base_url: "https://a.test/v1",
        api_key: "sk-test-aaaaaaaaaaaaaaaa", enabled: true, model: "claude-x",
        models: [{ name: "claude-x", enabled: true, priority: 1 }] },
      { id: "prov-wb", name: "网关B", protocol: "openai", base_url: `http://127.0.0.1:${gwPort}/v1`,
        api_key: "sk-test-bbbbbbbbbbbbbbbb", enabled: true, model: "gpt-y",
        allow_private: true,
        models: [{ name: "gpt-y", enabled: true, priority: 1 }] },
      { id: "prov-wc", name: "网关C", protocol: "auto", base_url: `http://127.0.0.1:${gwPort}/v1`,
        api_key: "sk-test-cccccccccccccccc", enabled: true, model: "gpt-z",
        allow_private: true,
        models: [{ name: "gpt-z", enabled: true, priority: 1 }] },
    ],
    bindings: { "claude-code": { chain: [
      { provider_id: "prov-wa", model: "claude-x" },
      { provider_id: "prov-wb", model: "gpt-y" },
      { provider_id: "prov-wc", model: "gpt-z" },
    ] } },
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
    /* 轮询等待条件成立：catalog 首帧为空、要等页面自己的 poll 拉回来，
     * 固定 sleep 会随机踩空（服务冷启动时尤其明显）。 */
    const waitFor = async (expr, ms = 20000) => {
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
    const ready = await waitFor(
      `(S.catalog||[]).filter(c=>c.installed&&c.orch_kind).length > 0 && (S.providers||[]).length >= 3`);
    check("页面数据就绪（catalog 已装 CLI + 供应商）", ready);

    // 前置：适配前，绑定页链上 openai 供应商显示 ⚠ 协议不匹配
    await evalJs(`switchTab("bindings"); "ok"`);
    await waitFor(`document.getElementById("binding-list").textContent.includes("协议不匹配")`);
    let chips = await evalJs(`document.getElementById("binding-list").textContent`);
    check("适配前：链上出现 ⚠ 协议不匹配", chips.includes("协议不匹配，解析时跳过"), chips.slice(0, 160));
    // auto 供应商没探过：链上也应显示 ⚠（不猜协议），而不是静默放行
    check("auto 未探测：链上同样 ⚠ 跳过",
      (chips.match(/协议不匹配，解析时跳过/g) || []).length >= 2, chips.slice(0, 300));

    // 0) auto 供应商在模型接入页显示「自动（未探测）」标签
    await evalJs(`switchTab("models"); "ok"`);
    await sleep(900);
    const provList = await evalJs(`document.getElementById("prov-list").textContent`);
    check("auto 供应商标签为「自动（未探测）」", provList.includes("自动（未探测）"), provList.slice(0, 200));

    // 1) 模型接入页：详情面板有「适配测试」按钮；选中外指本地假网关的供应商
    await evalJs(`selectProvider("prov-wb"); "ok"`);
    await sleep(700);
    const hasBtn = await evalJs(
      `document.getElementById("prov-detail").textContent.includes("适配测试")`);
    check("模型接入页有「适配测试」按钮", hasBtn === true);
    check("适配前：无 anthropic ✓ 徽标",
      (await evalJs(`document.getElementById("prov-detail").textContent.includes("anthropic ✓")`)) === false);

    // 1b) auto 供应商点适配测试 → 两条 wire 全探出来（分类）
    // 等待条件必须是「探测结束」本身：prov-detail 常驻的协议下拉里本来就有
    // anthropic/openai 字样，用 includes 判断会瞬间通过、随后断言抢跑。
    await evalJs(`selectProvider("prov-wc"); "ok"`);
    await sleep(600);
    await evalJs(`probeWire("prov-wc"); "ok"`);
    const autoProbed = await waitFor(
      `!!(S.probeState||{})["prov-wc"] && S.probeState["prov-wc"].busy !== true`);
    const wcDetail = await evalJs(`document.getElementById("prov-detail").textContent`);
    check("auto 适配测试探出两条 wire（caps 标签齐）",
      autoProbed && wcDetail.includes("anthropic ✓") && wcDetail.includes("openai ✓"),
      wcDetail.slice(0, 200));
    const capsApi = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const wc = (capsApi.providers || []).find((p) => p.id === "prov-wc");
    check("auto wire_caps 同时含 anthropic/openai",
      !!(wc && wc.wire_caps && wc.wire_caps.anthropic && wc.wire_caps.openai),
      JSON.stringify(wc && wc.wire_caps));
    const autoTag = await waitFor(
      `document.getElementById("prov-list").textContent.includes("自动 · anthropic/openai")`);
    check("auto 标签变为「自动 · anthropic/openai」", autoTag,
      (await evalJs(`document.getElementById("prov-list").textContent`)).slice(0, 200));

    // 1c) auto 供应商在同一条链上的芯片显示「自动 · %1 wire」（不是「已适配」）
    await evalJs(`switchTab("bindings"); poll(); "ok"`);
    const autoChip = await waitFor(
      `document.getElementById("binding-list").textContent.includes("自动 · anthropic wire")`);
    check("auto 链芯片显示「自动 · anthropic wire」", autoChip,
      (await evalJs(`document.getElementById("binding-list").textContent`)).slice(0, 300));
    await evalJs(`switchTab("models"); "ok"`);
    await sleep(600);

    // 2) 点「适配测试」→ 探测本地假网关 anthropic wire → 徽标落下
    await evalJs(`selectProvider("prov-wb"); "ok"`);
    await sleep(600);
    await evalJs(`probeWire("prov-wb"); "ok"`);
    const probed = await waitFor(
      `document.getElementById("prov-detail").textContent.includes("anthropic ✓")`);
    const after = await evalJs(`document.getElementById("prov-detail").textContent`);
    check("适配测试后出现「已适配」结果", probed && after.includes("已适配"), after.slice(0, 160));
    check("适配测试后落下 anthropic ✓ 徽标", after.includes("anthropic ✓"), "");
    const caps = await fetch(SERVICE + "/api/models").then((r) => r.json());
    const wb = (caps.providers || []).find((p) => p.id === "prov-wb");
    check("wire_caps 落盘（anthropic 面）", !!(wb && wb.wire_caps && wb.wire_caps.anthropic &&
      wb.wire_caps.anthropic.base), JSON.stringify(wb && wb.wire_caps));

    // 2b) 适配面徽章要落到模型列表与供应商卡片上——只留在详情头的话，
    // 列表看上去永远只有原生协议一种面（2026-09-19 用户反馈：OpenAI 面不可见）
    const gtWb = await evalJs(
      `(document.querySelector("#pm-groups .pgroup-title")||{textContent:""}).textContent`);
    check("模型分组标题出现另一条 wire 徽章（anthropic ✓）",
      gtWb.includes("anthropic ✓"), gtWb);
    const listWb = await evalJs(`document.getElementById("prov-list").textContent`);
    check("供应商卡片出现另一条 wire 徽章", listWb.includes("anthropic ✓"),
      listWb.slice(0, 200));
    await evalJs(`selectProvider("prov-wc"); "ok"`);
    await sleep(600);
    const gtWc = await evalJs(
      `(document.querySelector("#pm-groups .pgroup-title")||{textContent:""}).textContent`);
    check("auto 供应商分组标题亮出两条实测 wire",
      gtWc.includes("anthropic ✓") && gtWc.includes("openai ✓"), gtWc);
    await shot("wire-adapt-models.png");

    // 2c) chat 形态的适配面要标「· chat」、codex 死因要点名 chat 形态——
    // 否则模型接入页 ✓ 与绑定页 ⚠ 协议不匹配互相打脸（2026-09-19 维云案：
    // 网关 /responses 开始要求 workspaceid，探针落到 chat 形态）
    await evalJs(`(function(){ var pa=S.providers.find(function(x){return x.id==="prov-wa";});
      pa.wire_caps={openai:{base:"https://x.test/v1", wire_api:"chat"}}; })(); S.modelsSig=null; renderModels(); "ok"`);
    await sleep(500);
    const cardChat = await evalJs(`document.getElementById("prov-list").textContent`);
    check("chat 形态徽章标出「· chat」", cardChat.includes("openai ✓ · chat"),
      cardChat.slice(0, 200));
    const deadChat = await evalJs(
      `String((chainDeadReasons({orch_kind:"codex"}, [{p:"prov-wa", m:"claude-x"}])[0]) || "")`);
    check("codex 死因细化为「chat 形态」文案", deadChat.includes("chat 形态"), deadChat);
    await evalJs(`(function(){ var pa=S.providers.find(function(x){return x.id==="prov-wa";});
      delete pa.wire_caps.openai; })(); S.modelsSig=null; renderModels(); "ok"`);
    await sleep(300);

    // 3) 绑定页：同一条链的 ⚠ 变「✓ 已适配」；真源 /api/models 同步
    await evalJs(`switchTab("bindings"); poll(); "ok"`);
    const bound = await waitFor(
      `document.getElementById("binding-list").textContent.includes("已适配（anthropic wire）")`);
    chips = await evalJs(`document.getElementById("binding-list").textContent`);
    check("适配后：⚠ 消失", !chips.includes("协议不匹配，解析时跳过"), chips.slice(0, 200));
    check("适配后：链芯片显示 ✓ 已适配（anthropic wire）", bound, chips.slice(0, 200));
    await shot("wire-adapt-bindings.png");

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    try { gw.close(); } catch (e) { /* ignore */ }
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
  console.log("\n===== wire 适配 UI/CDP：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
