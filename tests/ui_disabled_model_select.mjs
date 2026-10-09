/* 停用模型/无密钥厂商选择层闸门（Edge headless + CDP）。
 * 0.1.98 排查定版：三处选择器（Composer 隐藏真源 / 定时任务弹窗 / 运行详情对话条）
 * 只滤 hidden 漏了 enabled，停用模型照列可选；modelGroups 不查密钥，无 KEY 厂商
 * 能配出死链。本套断言：停用模型不进任何选择器、模型全停的厂商不进对话厂商下拉、
 * 无密钥厂商在绑定面板标死不可勾。临时数据目录 + 独立端口，不碰真实 data/ 与 8765。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18863;
const CDP_PORT = 9427;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
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
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-dismodel-"));
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
    check("临时服务就绪(" + SERVICE_PORT + ")", up);

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
      if (msg.method === "Runtime.exceptionThrown") {
        const d = msg.params.exceptionDetails || {};
        const line = (d.exception && (d.exception.description || d.exception.value)) || d.text;
        consoleErrors.push(String(line).split("\n").slice(0, 3).join(" / "));
      }
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

    // 种盘（页面内发写请求自动接管设备控制权；避开服务端 curl 写的 423 竞态）：
    // 健康厂商（m-ok 启用 + m-dead 停用，默认模型指向停用的 m-dead）、
    // 裸厂商（无密钥，有启用模型）、全停厂商（有密钥，模型全停用）
    const R = await evalJson(`(async () => {
      const out = {};
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      const api = async (path, body) => {
        const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error) out["err@" + path] = (j.error || ("HTTP " + r.status));
        return j;
      };

      await api("/api/models/provider", { name: "健康厂商", protocol: "openai",
        base_url: "https://dmodel-h.test/v1", api_key: "sk-test-" + "aaaa".repeat(8), model: "m-dead" });
      let models = await fetch("/api/models").then((r) => r.json());
      const pidOk = (models.providers || []).find((p) => p.name === "健康厂商");
      out.seededOk = !!pidOk;
      if (!pidOk) return out;
      await api("/api/models/add", { id: pidOk.id, name: "m-ok" });
      await api("/api/models/add", { id: pidOk.id, name: "m-dead" });
      await api("/api/models/model-op", { provider_id: pidOk.id, names: ["m-dead"], op: "disable" });

      await api("/api/models/provider", { name: "裸厂商", protocol: "openai",
        base_url: "https://dmodel-b.test/v1" });
      models = await fetch("/api/models").then((r) => r.json());
      const pidBare = (models.providers || []).find((p) => p.name === "裸厂商");
      await api("/api/models/add", { id: pidBare.id, name: "m-bare" });

      await api("/api/models/provider", { name: "全停厂商", protocol: "openai",
        base_url: "https://dmodel-x.test/v1", api_key: "sk-test-" + "bbbb".repeat(8), model: "m-x" });
      models = await fetch("/api/models").then((r) => r.json());
      const pidOff = (models.providers || []).find((p) => p.name === "全停厂商");
      await api("/api/models/add", { id: pidOff.id, name: "m-x" });
      await api("/api/models/model-op", { provider_id: pidOff.id, names: ["m-x"], op: "disable" });

      // 刷新前端状态后逐层断言：直接拉 /api/models 赋真源（poll 的 lookup
      // 有在飞缓存 + 30s 节流，会返回种盘前的旧数据，不可依赖）
      const fresh = await fetch("/api/models").then((r) => r.json());
      S.providers = fresh.providers; S.bindings = fresh.bindings;
      S.modelCatalog = fresh.catalog || [];
      S.provSig = "";
      renderDirectModelPicker();
      await wait(300);
      const optVals = (sel) => Array.from($(sel).options).map((o) => o.value);

      // 1) directProviders：全停厂商（启用模型数为零）不进对话厂商下拉数据源
      out.directNames = directProviders().map((p) => p.name);

      // 2) modelGroups：健康/裸在列（带 keyOk 标记），全停不在列
      out.groups = modelGroups().map((g) => ({ name: g.name, keyOk: g.keyOk, models: g.models }));

      // 3) Composer 隐藏真源 select：选中健康厂商后模型下拉无 m-dead
      renderDirectModelPicker();
      $("f-direct-provider").value = pidOk.id;
      renderDirectModelPicker();
      out.cmpModelOpts = optVals("f-direct-model-name");

      // 4) 目录页默认模型下拉（modelSelectHtml）：无密钥厂商不进组，停用模型不进选项
      const selHtml = modelSelectHtml("dmg", "", "opencode");
      out.selHasDead = selHtml.includes("m-dead");
      out.selHasBareGroup = selHtml.includes("裸厂商");
      out.selHasOk = selHtml.includes("m-ok");

      // 5) chainProvUsable：无密钥厂商不推荐；绑定面板里无密钥条目标死禁勾
      const allow = bindAllowedProtocols("opencode");
      const pvOk = (S.providers || []).find((p) => p.id === pidOk.id);
      const pvBare = (S.providers || []).find((p) => p.id === pidBare.id);
      out.recommendOkUsable = chainProvUsable(pvOk, allow, "opencode");
      out.recommendBareUsable = chainProvUsable(pvBare, allow, "opencode");
      // 不依赖真机装了哪个 CLI：直接造目录条目调 bindPanel 看渲染口径
      const fakeCli = { id: "dmg-fake-opencode", name: "FakeOpenCode",
        installed: true, orch_kind: "opencode" };
      S.bindings = S.bindings || {};
      S.bindings[fakeCli.id] = { chain: [] };
      out.bindPanelHtml = bindPanel(fakeCli);

      // 6) 定时任务弹窗：对话厂商下拉无全停厂商，模型下拉无 m-dead
      await autoForm();
      await wait(200);
      $("au-flow").value = "direct";
      $("au-flow").dispatchEvent(new Event("change"));
      await wait(120);
      out.auProvOpts = optVals("au-direct-provider").map((v) => {
        const o = Array.from($("au-direct-provider").options).find((x) => x.value === v);
        return o ? o.textContent : v;
      });
      $("au-direct-provider").value = pidOk.id;
      $("au-direct-provider").dispatchEvent(new Event("change"));
      await wait(120);
      out.auModelOpts = optVals("au-direct-model-name");
      closeModal();
      return out;
    })()`);

    if (!R || R.__err) {
      check("浏览器端评估执行", false, JSON.stringify(R));
    } else {
      check("种盘：健康厂商就位", R.seededOk);
      const errKeys = Object.keys(R).filter((k) => k.startsWith("err@"));
      check("种盘写操作无报错", errKeys.length === 0,
        errKeys.map((k) => k + "=" + R[k]).join(" | "));
      check("directProviders 排除模型全停厂商",
        JSON.stringify(R.directNames) === JSON.stringify(["健康厂商"]), JSON.stringify(R.directNames));
      const g = (n) => (R.groups || []).find((x) => x.name === n);
      check("modelGroups：全停厂商不列", !g("全停厂商"), JSON.stringify(R.groups));
      check("modelGroups：健康厂商 keyOk 且只含启用模型",
        g("健康厂商") && g("健康厂商").keyOk === true &&
        JSON.stringify(g("健康厂商").models) === JSON.stringify(["m-ok"]), JSON.stringify(R.groups));
      check("modelGroups：无密钥厂商 keyOk=false",
        g("裸厂商") && g("裸厂商").keyOk === false, JSON.stringify(R.groups));
      check("Composer 模型下拉无停用模型",
        JSON.stringify(R.cmpModelOpts) === JSON.stringify(["", "m-ok"]), JSON.stringify(R.cmpModelOpts));
      check("目录页默认模型下拉无停用模型", R.selHasOk && !R.selHasDead,
        "ok=" + R.selHasOk + " dead=" + R.selHasDead);
      check("目录页默认模型下拉不含无密钥厂商组", !R.selHasBareGroup);
      check("chainProvUsable：健康=true 无密钥=false",
        R.recommendOkUsable === true && R.recommendBareUsable === false,
        "ok=" + R.recommendOkUsable + " bare=" + R.recommendBareUsable);
      check("绑定面板：无密钥条目标死禁勾",
        R.bindPanelHtml && R.bindPanelHtml.includes("无可用密钥") &&
        R.bindPanelHtml.includes("oitem dim") && /value="m-bare"[^>]*disabled/.test(R.bindPanelHtml),
        String(R.bindPanelHtml || "").slice(0, 200));
      check("定时任务厂商下拉无全停厂商",
        !(R.auProvOpts || []).includes("全停厂商"), JSON.stringify(R.auProvOpts));
      check("定时任务模型下拉无停用模型",
        JSON.stringify(R.auModelOpts) === JSON.stringify(["", "m-ok"]), JSON.stringify(R.auModelOpts));
    }
    check("无控制台报错", consoleErrors.length === 0, consoleErrors.join(" | "));
  } finally {
    // 按 PID 杀树清理（不按映像名连坐），并验证端口归还
    try { spawn("taskkill", ["/PID", String(srv.pid), "/T", "/F"], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" }); } catch (e) {}
    await sleep(1500);
    let freed = true;
    try { const r = await fetch(`${SERVICE}/api/state`, { signal: AbortSignal.timeout(1500) }); freed = !r.ok; }
    catch (e) { freed = true; }
    check("端口归还自证(" + SERVICE_PORT + ")", freed);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }

  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `FAILED: ${failed.length}/${results.length}` : `ALL PASS: ${results.length}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
