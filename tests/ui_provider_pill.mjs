/* 侧栏左下角「编排者供应商」指示：显示当前 Tutti 智能体用的厂商，点击进设置更换。
 * 覆盖：未选/生效中/不生效三态、点击跳转编排中枢、下拉范围＝已接入（有密钥）的厂商、
 * 页内改选后侧栏即时同步。
 *
 * 注意（踩过的坑）：Tutti 的写接口都需要设备控制权——控制权空闲时由首个写请求自动接管，
 * 之后其他设备再写就 423。所以本脚本**所有数据变更都从页面内发起**（同一设备身份），
 * 并在开始前显式抢到控制权；用 Node 直发 POST 会和页面抢控制权，导致页面写全被拒。
 *
 * 用法：起 18799 临时服务（建议空数据目录），再 node tests/ui_provider_pill.mjs */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18799";
const CDP_PORT = 9337;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 280)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 固定的测试数据：甲/乙有密钥（可作编排者），丙无密钥（不该出现在可选范围）
const P_A = { name: "测试甲", protocol: "openai", base_url: "https://api.a.example.com", api_key: "sk-aaa", model: "gpt-4o-mini" };
const P_B = { name: "测试乙", protocol: "anthropic", base_url: "https://api.b.example.com", api_key: "sk-bbb", model: "claude-sonnet-4" };
const P_C = { name: "无密钥丙", protocol: "openai", base_url: "https://api.c.example.com", api_key: "", model: "nokey-model" };

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-pv-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);

    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const dialogs = [];
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时无响应：" + method)); }, 20000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      // 写操作被控制权拒绝时会走 alert；自动打掉，否则渲染线程被堵死、后续 evaluate 全挂
      else if (m.method === "Page.javascriptDialogOpening") {
        dialogs.push(m.params.type + "：" + m.params.message);
        send("Page.handleJavaScriptDialog", { accept: true });
      }
    };
    const evalJs = async (x) => {
      // awaitPromise：表达式是 async IIFE 时等它 resolve，否则拿到的是 Promise 对象
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面内表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 300));
      return r.result?.result?.value;
    };
    /* 服务端真值：只读 GET，不受设备控制权影响，用来校验「到底存了什么」 */
    const serverProvs = async () => (await fetch(SERVICE + "/api/models").then((r) => r.json())).providers;
    /* 页面内调用接口（复用页面的设备身份与请求头，避免与 Node 直发抢控制权）。
     * api() 对非 2xx 会抛错，所以这里 ok 即代表 HTTP 成功。 */
    const pageApi = async (path, body, method = "POST") => JSON.parse(await evalJs(
      `(async () => { try { const r = await api(${JSON.stringify(path)}, ${method === "GET"
        ? "{method:'GET'}"
        : `{method:${JSON.stringify(method)}, body: ${JSON.stringify(JSON.stringify(body || {}))}}`}); return JSON.stringify({ok:true, r}); }
        catch (e) { return JSON.stringify({ok:false, err:e.message}); } })()`));
    const reload = async () => { await send("Page.navigate", { url: SERVICE + "/" }); await sleep(3500); };
    const takeControl = async () => {
      await evalJs(`(async () => { await ctrlAction("acquire", true); return "ok"; })()`);
      for (let i = 0; i < 24; i++) {
        if (await evalJs(`!!(S.control && S.control.mine)`)) return true;
        await sleep(250);
      }
      return false;
    };
    const pill = () => evalJs(`(() => {
      const b = document.getElementById("btn-prov-side");
      const r = b.getBoundingClientRect();
      return JSON.stringify({
        text: document.getElementById("prov-side-text").textContent.trim(),
        dot: document.getElementById("prov-side-dot").className,
        title: b.title, visible: r.width > 0 && r.height > 0,
        w: Math.round(r.width), h: Math.round(r.height),
        x: Math.round(r.x),
      });
    })()`);

    await send("Page.enable");
    await reload();

    /* ---- 0) 准备数据：抢控制权 → 清空供应商 → 建 3 个（可重复运行，不受上次残留影响） ---- */
    check("页面取得控制权", await takeControl());
    const before = await serverProvs();   // 以服务端为准，不用页面缓存（可能还没拉到）
    if (before.length) {
      const r = await pageApi("/api/models/provider-op", { ids: before.map((p) => p.id), op: "delete" });
      if (!r.ok) check("清空既有供应商", false, r.err);
    }
    const cleared = await serverProvs();
    check("清空既有供应商", cleared.length === 0, JSON.stringify(cleared.map((p) => p.name)));
    for (const p of [P_A, P_B, P_C]) {
      const r = await pageApi("/api/models/provider", p);
      if (!r.ok) check("建供应商 " + p.name, false, r.err);
    }
    const created = await serverProvs();
    check("三个测试供应商已就位（2 个有密钥）",
      created.length === 3 && created.filter((p) => p.api_key).length === 2,
      JSON.stringify(created.map((p) => [p.name, !!p.api_key])));

    // 重载页面：让 S.providers 与服务端一致，后续断言基于页面真实状态
    await reload();
    const pageProvs = JSON.parse(await evalJs(`JSON.stringify((S.providers || []).map(p => [p.id, p.name, !!p.api_key]))`));
    const plist = created.map((p) => [p.id, p.name, !!p.api_key]);
    check("页面已加载到全部供应商", pageProvs.length === 3, JSON.stringify({ pageProvs, plist }));
    const withKey = plist.filter((p) => p[2]);
    const keyless = plist.filter((p) => !p[2]);

    /* ---- 1) 未选厂商：占位文案 + 灰点 ---- */
    await pageApi("/api/orchestrator", { provider_id: "", enabled: false });
    await reload();
    const empty = JSON.parse(await pill());
    check("未选厂商时显示占位并可见", empty.visible && empty.text === "未选厂商", JSON.stringify(empty));
    check("未选厂商用灰点", /^pdot$/.test(empty.dot), empty.dot);
    check("未选厂商的悬停提示说明去哪选", /编排中枢/.test(empty.title), empty.title);

    /* ---- 2) 选一个已接入厂商：显示其名 + 绿点（生效中） ---- */
    await pageApi("/api/orchestrator", { provider_id: withKey[0][0], model: P_A.model, enabled: true });
    await reload();
    const on = JSON.parse(await pill());
    check("选中后显示该厂商名", on.text === P_A.name, JSON.stringify(on));
    check("生效中用绿点", /pdot ok/.test(on.dot), on.dot);
    check("悬停提示含厂商名与生效状态", on.title.includes(P_A.name) && /生效中/.test(on.title), on.title);

    /* ---- 3) 供应商被停用：仍显示其名，但转黄点（不生效） ---- */
    await pageApi("/api/models/provider-op", { ids: [withKey[0][0]], op: "disable" });
    await reload();
    const dis = JSON.parse(await pill());
    check("停用后仍显示其名（配置没丢）", dis.text === P_A.name, JSON.stringify(dis));
    check("停用后转为黄点（未生效）", /pdot warn/.test(dis.dot), dis.dot);
    check("悬停提示给出未生效原因", /未生效/.test(dis.title), dis.title);
    await pageApi("/api/models/provider-op", { ids: [withKey[0][0]], op: "enable" });
    await sleep(400);

    /* ---- 4) 点击 → 进设置里的「编排中枢」页 ---- */
    await reload();
    await evalJs(`document.getElementById("btn-prov-side").click(); "ok"`);
    await sleep(1300);
    const jumped = JSON.parse(await evalJs(`JSON.stringify({
      settingsMode: document.body.classList.contains("settings-mode"),
      title: document.getElementById("page-title").textContent,
      active: (document.querySelector(".set-item.active") || {}).dataset?.sub || "",
      orchShown: !document.getElementById("sub-orch").classList.contains("hidden"),
      hasSelect: !!document.getElementById("orch-prov")
    })`));
    check("点击后进入设置页的编排中枢",
      jumped.settingsMode && jumped.active === "orch" && jumped.orchShown && jumped.hasSelect,
      JSON.stringify(jumped));
    check("顶栏标题同步为编排中枢", jumped.title === "编排中枢", jumped.title);

    /* ---- 5) 下拉范围＝已接入（有密钥）的厂商，无密钥的不可选 ---- */
    const opts = JSON.parse(await evalJs(`JSON.stringify(
      [...document.getElementById("orch-prov").options].map(o => [o.value, o.textContent]))`));
    const ids = opts.map((o) => o[0]).filter(Boolean);
    check("下拉列出全部已接入厂商",
      withKey.every((p) => ids.includes(p[0])) && ids.length === withKey.length,
      JSON.stringify({ ids, want: withKey.map((p) => p[0]) }));
    check("无密钥的厂商不出现在可选范围",
      keyless.every((p) => !ids.includes(p[0])), JSON.stringify({ ids, keyless }));
    check("下拉当前项＝侧栏显示的厂商",
      await evalJs(`document.getElementById("orch-prov").value`) === withKey[0][0], withKey[0][0]);

    /* ---- 6) 页内改选另一厂商并保存 → 侧栏指示同步 + 落盘 ---- */
    const p2id = withKey[1][0], p2name = withKey[1][1];
    await evalJs(`(() => {
      const s = document.getElementById("orch-prov");
      s.value = ${JSON.stringify(p2id)};
      s.dispatchEvent(new Event("change"));
      document.getElementById("orch-enabled").checked = true;
      return "ok";
    })()`);
    await sleep(400);
    // 换供应商会刷新模型下拉：若整块重绘，下拉会被 S.orch 弹回旧值，这里就是把关点
    const afterChange = await evalJs(`document.getElementById("orch-prov").value`);
    check("切换供应商后下拉不回弹（改动可保存）", afterChange === p2id,
      JSON.stringify({ afterChange, want: p2id }));
    await evalJs(`saveOrchestrator(); "ok"`);
    await sleep(1800);
    const synced = JSON.parse(await pill());
    check("保存后侧栏指示即时同步到新厂商", synced.text === p2name, JSON.stringify({ synced, want: p2name }));
    check("写入未被控制权拦截（无 423 弹窗）",
      dialogs.filter((d) => /控制/.test(d)).length === 0, JSON.stringify(dialogs));
    const persisted = await fetch(SERVICE + "/api/orchestrator").then((r) => r.json());
    check("改动已落盘到服务端", persisted.orchestrator.provider_id === p2id,
      JSON.stringify(persisted.orchestrator));

    /* ---- 7) 布局：夹在设置与手机连接两个图标之间，且不挤压它们 ----
     * 先退回任务视图：设置模式下 .side-main 整体 display:none，量到的是全 0 */
    await evalJs(`exitSettings(); "ok"`);
    await sleep(600);
    const geo = JSON.parse(await evalJs(`(() => {
      const r = (id) => { const b = document.getElementById(id).getBoundingClientRect();
        return { l: Math.round(b.left), r: Math.round(b.right), y: Math.round(b.top), h: Math.round(b.height) }; };
      return JSON.stringify({ gear: r("btn-settings"), prov: r("btn-prov-side"), phone: r("btn-phone-side") });
    })()`));
    check("位于设置与手机连接之间且互不重叠",
      geo.gear.r <= geo.prov.l && geo.prov.r <= geo.phone.l && geo.prov.l < geo.prov.r,
      JSON.stringify(geo));
    check("与两侧图标按钮等高对齐", geo.gear.y === geo.prov.y && geo.prov.h === geo.gear.h, JSON.stringify(geo));
    check("指示宽度占满中间剩余空间", geo.prov.r - geo.prov.l > 60, JSON.stringify(geo));

    const shot = await send("Page.captureScreenshot", { format: "png" });
    if (shot?.result?.data) {
      writeFileSync(join(ROOT, ".ui-shots", "r3-prov-pill.png"), Buffer.from(shot.result.data, "base64"));
    }
    await evalJs(`document.getElementById("btn-prov-side").click(); "ok"`);
    await sleep(900);
    const shot2 = await send("Page.captureScreenshot", { format: "png" });
    if (shot2?.result?.data) {
      writeFileSync(join(ROOT, ".ui-shots", "r3-prov-pill-settings.png"), Buffer.from(shot2.result.data, "base64"));
    }

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((x) => !x).length;
  console.log("\n===== 编排者供应商指示：%d 通过 / %d 失败 =====", results.length - bad, bad);
  if (bad) process.exit(1);
}

main().catch((e) => {
  const bad = results.filter((x) => !x).length;
  console.error("FATAL", e);
  console.log("\n===== 编排者供应商指示（中断）：%d 通过 / %d 失败 =====", results.length - bad, bad);
  process.exit(1);
});
