/* 厂商/模型停用·启用后的调度链语义验收（2026-09-22 拍板：禁用即出调度，
 * 不自动换将、不回填——调度只在用户手动「一键推荐」时才动）：
 * 1) 停用链首厂商 → 链上它的条目被后端剔除，其余条目原样保留（不插推荐）
 * 2) 停用后重新启用 → 已剔除的条目不回填
 * 3) 停用模型 → 只摘 (厂商, 模型) 那一条，链内其余键保留
 * 4) 全程无「自动重绑」类落盘：空绑定与 CLI 空默认模型保持自动模式
 * 自含临时服务（端口 18795）：TUTTI_DATA + USERPROFILE 双临时，不碰真实配置。
 * 用法：node tests/ui_autorebind_toggle.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18795;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9353;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-rebind-"));
  const dataDir = join(tmp, "data");
  const fakeHome = join(tmp, "home");
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(join(fakeHome, "faketools"), { recursive: true });
  for (const f of ["auto-a.exe", "auto-b.exe", "auto-c.exe"])
    writeFileSync(join(fakeHome, "faketools", f), "fake");
  // prov-x/prov-y 健康可推荐；prov-bad 默认模型 gpt-dead 已隐藏（坏默认，
  // 必须被推荐排除——即使它带另一个可用模型 gpt-live）
  writeFileSync(join(dataDir, "models.json"), JSON.stringify({
    providers: [
      { id: "prov-x", name: "厂商X", protocol: "openai", priority: 1,
        base_url: "https://t.test/v1", api_key: "sk-x" + "x".repeat(20),
        models: [{ name: "gpt-x", priority: 1 }] },
      { id: "prov-y", name: "厂商Y", protocol: "openai", priority: 2,
        base_url: "https://t.test/v1", api_key: "sk-y" + "y".repeat(20),
        models: [{ name: "gpt-y", priority: 1 }] },
      { id: "prov-bad", name: "厂商B", protocol: "openai", priority: 3, model: "gpt-dead",
        base_url: "https://t.test/v1", api_key: "sk-b" + "b".repeat(20),
        models: [{ name: "gpt-dead", priority: 1, hidden: true },
                 { name: "gpt-live", priority: 2 }] },
    ],
    bindings: { "auto-a": { chain: [{ provider_id: "prov-x", model: "gpt-x" },
                                     { provider_id: "prov-y", model: "gpt-y" }] } },
  }));
  // auto-a：停用剔除验收目标（kind=codex 只吃 openai）；auto-b：目录空默认模型
  // 不被写盘的对照；auto-c：kind=claude 而无 anthropic 厂商的对照
  writeFileSync(join(dataDir, "catalog.json"), JSON.stringify([
    { id: "auto-a", name: "重绑甲", note: "绑定目标", cli_group: "installed",
      detect: { exe: "~/faketools/auto-a.exe" },
      orch: { kind: "codex", command: "auto-a" },
      config: { path: "~/.auto-a/x.toml", format: null, model_key: null },
      default_enabled: false },
    { id: "auto-b", name: "重绑乙", note: "目录对照", cli_group: "installed",
      detect: { exe: "~/faketools/auto-b.exe" },
      orch: { kind: "codex", command: "auto-b" },
      config: { path: "~/.auto-b/settings.json", format: "json", model_key: "model" },
      default_enabled: false },
    { id: "auto-c", name: "重绑丙", note: "无合适推荐", cli_group: "installed",
      detect: { exe: "~/faketools/auto-c.exe" },
      orch: { kind: "claude", command: "auto-c" },
      config: { path: "~/.auto-c/settings.json", format: "json", model_key: "model" },
      default_enabled: false },
  ]));

  let edge = null, ws = null, svc = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, USERPROFILE: fakeHome, PYTHONPATH: ROOT },
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
    // 等供应商与绑定装进页面状态（首/catalog 探测完成后）
    for (let i = 0; i < 30; i++) {
      const ok = await js(`(S.providers || []).length >= 3 && S.bindings && S.bindings["auto-a"] ? "ok" : ""`);
      if (ok === "ok") break;
      await sleep(1000);
    }
    // 页内绕过确认弹框（测试直接驱动停用动作）
    await js(`window.uiConfirm = async () => true; "stubbed"`);

    const chainOf = async () => {
      const st = await (await fetch(SERVICE + "/api/models")).json();
      return ((st.bindings || {})["auto-a"] || {}).chain || [];
    };
    const noAutoRebindToast = async (tag) => {
      const toast = await js(`(document.getElementById("toast") || {}).textContent || ""`);
      check(tag + "：无「自动重绑」类落盘提示（不动调度）",
        (toast || "").indexOf("自动重绑") < 0, JSON.stringify(toast));
    };

    // ---- 第一步：停用链首厂商 prov-x → 它的条目出链，其余原样 ----
    await js(`toggleProviderEnabled("prov-x", false); "toggling"`);
    await sleep(2500);
    const chainA = await chainOf();
    check("停用链首厂商：条目直接出链，不换将不插推荐",
      JSON.stringify(chainA) === JSON.stringify([{ provider_id: "prov-y", model: "gpt-y" }]),
      JSON.stringify(chainA));
    const cat1 = await (await fetch(SERVICE + "/api/catalog")).json();
    const cmap = {};
    for (const c of cat1.catalog || []) cmap[c.id] = c;
    check("停用厂商：auto-b 空默认模型保持不写盘",
      !(cmap["auto-b"] || {}).model, JSON.stringify(cmap["auto-b"] || {}).slice(0, 120));
    check("停用厂商：auto-c（claude 无厂商）保持未绑",
      !(cmap["auto-c"] || {}).model, JSON.stringify((cmap["auto-c"] || {}).model));
    await noAutoRebindToast("停用厂商");
    check("停用厂商：绑定页无脏草稿残留",
      await js(`Object.values(S.bindSel).every((st) => !st.dirty) ? "ok" : "dirty"`) === "ok", "");

    // ---- 第二步：推荐源判定仍在（手动「一键推荐」的评分源不受影响）----
    const recBefore = await js(`JSON.stringify(recommendFor({ orch_kind: "codex" }))`);
    check("推荐源：prov-x 停用后推荐到 prov-y",
      recBefore === JSON.stringify({ p: "prov-y", m: "gpt-y" }), recBefore);
    await js(`toggleProviderEnabled("prov-y", false); "toggling2"`);
    await sleep(2500);
    const recAfter = await js(`JSON.stringify(recommendFor({ orch_kind: "codex" }))`);
    check("推荐源：只剩坏默认厂商时推荐为空（不兜底）", recAfter === "null", recAfter);
    const chainA2 = await chainOf();
    check("停用第二厂商：auto-a 链条目全部出链（空链=回落 CLI 默认）",
      chainA2.length === 0, JSON.stringify(chainA2));
    await noAutoRebindToast("停用第二厂商");

    // ---- 第三步：重新启用 → 已剔除的条目不回填 ----
    await js(`toggleProviderEnabled("prov-x", true); "toggling3"`);
    await js(`toggleProviderEnabled("prov-y", true); "toggling4"`);
    await sleep(2500);
    const chainA3 = await chainOf();
    check("重新启用：链不回填（仍为空，要回链用「一键推荐」）",
      chainA3.length === 0, JSON.stringify(chainA3));
    const cat3 = await (await fetch(SERVICE + "/api/catalog")).json();
    const cmap3 = {};
    for (const c of cat3.catalog || []) cmap3[c.id] = c;
    check("重新启用：auto-b 仍未写入 CLI 默认模型",
      !(cmap3["auto-b"] || {}).model, JSON.stringify((cmap3["auto-b"] || {}).model));

    // ---- 第四步：模型级停用 → 只摘 (厂商, 模型) 那一条 ----
    const setChain = await js(`api("/api/models/binding", { method: "POST", busy: false,
      body: JSON.stringify({ agent_id: "auto-a", provider_id: "prov-x",
        chain: [{ provider_id: "prov-x", model: "gpt-x" },
                { provider_id: "prov-y", model: "gpt-y" }],
        difficulty_routing: false }) }).then((r) => r.ok ? "ok" : JSON.stringify(r))`);
    check("重建双条目链（页面 api 通道）", setChain === "ok", JSON.stringify(setChain));
    await js(`modelOp("prov-x", "gpt-x", "disable"); "mdis"`); 
    await sleep(2500);
    const chainA4 = await chainOf();
    check("停用模型：只摘 (prov-x, gpt-x)，prov-y 条目保留",
      JSON.stringify(chainA4) === JSON.stringify([{ provider_id: "prov-y", model: "gpt-y" }]),
      JSON.stringify(chainA4));
    await js(`modelOp("prov-x", "gpt-x", "enable"); "mena"`);
    await sleep(2500);
    const chainA5 = await chainOf();
    check("重新启用模型：链不回填", JSON.stringify(chainA5) === JSON.stringify([{ provider_id: "prov-y", model: "gpt-y" }]),
      JSON.stringify(chainA5));

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
    await sleep(500);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  console.log("\n===== 停用即出调度验收：%d 通过 / %d 失败 =====", PASS.length, FAIL.length);
  if (FAIL.length) { console.log("失败项：", FAIL); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
