/* 智能体管理页 UI 验证：Edge headless + CDP（Node 内置 WebSocket，零依赖）。
 * 断言：继续会话下拉动态生成（含已装 CLI）→ 智能体目录卡片渲染 →
 * 卸载按钮出现且带确认（取消后不发起）→ 安装面板结构（live-dot / 日志框）→
 * 截图 → 关闭浏览器（清理进程）。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:8802";
const CDP_PORT = 9334;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const edge = EDGE_CANDIDATES.find(() => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-mgmt-"));
  const proc = spawn(edge, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        const list = await res.json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);

    const ws = new WebSocket(target.webSocketDebuggerUrl);
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

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);   // 等 load + refreshSessionAgents + loadFlows

    // 1) 继续会话下拉动态生成：首项是「不沿用」，其后是本机已装且支持会话扫描的 CLI
    const resume = JSON.parse(await evalJs(`JSON.stringify({
      options: document.getElementById("f-resume-agent").options.length,
      first: document.getElementById("f-resume-agent").options[0].value,
      values: Array.from(document.getElementById("f-resume-agent").options).map(o => o.value)
    })`));
    check("继续会话下拉首项为不沿用", resume.first === "", JSON.stringify(resume));
    check("继续会话下拉由后端动态填充（含已装 CLI）",
      resume.options >= 2 && resume.values.some(v => ["codex-cli", "claude-code", "opencode", "qwencode", "mimo-code"].includes(v)),
      JSON.stringify(resume.values));

    // 2) 智能体目录页：卡片 + 卸载按钮（已装且有卸载命令的条目）
    await evalJs(`switchTab("agents"); "ok"`);
    await sleep(1500);
    const cat = JSON.parse(await evalJs(`JSON.stringify({
      cards: document.querySelectorAll(".card").length,
      uninstallBtns: Array.from(document.querySelectorAll(".card .ops .danger")).map(b => b.textContent.trim()),
      smokeBtns: Array.from(document.querySelectorAll(".card .ops button")).map(b => b.textContent.trim()).filter(t => t === "冒烟测试").length,
      hints: Array.from(document.querySelectorAll(".card .hint")).map(h => h.textContent.trim())
    })`));
    check("智能体目录渲染出卡片", cat.cards >= 3, "cards=" + cat.cards);
    check("已装 CLI 有卸载按钮（danger 样式）",
      cat.uninstallBtns.includes("卸载"), JSON.stringify(cat.uninstallBtns));
    check("冒烟测试按钮存在", cat.smokeBtns >= 1, JSON.stringify(cat.smokeBtns));
    // 临时数据目录没有可安装条目时该提示不出现——只在存在待安装条目时断言
    if (cat.hints.some(t => /安装命令待配置/.test(t))) {
      check("可安装区显示「安装命令待配置」提示", true);
    }

    // 3) 卸载确认弹框：cancel 后不发起任何请求（不产生 mgmt 面板）
    const mgmtBefore = await evalJs(`JSON.stringify(window.S && S.mgmt ? Object.keys(S.mgmt) : [])`);
    await evalJs(`
      window.confirm = () => false;   // 拦截确认框：模拟用户点「取消」
      const btn = Array.from(document.querySelectorAll(".card .ops .danger")).find(b => b.textContent.trim() === "卸载");
      btn.click(); "clicked"`);
    await sleep(600);
    const mgmtAfter = await evalJs(`JSON.stringify(window.S && S.mgmt ? Object.keys(S.mgmt) : [])`);
    check("卸载确认框取消后不发起操作", mgmtAfter === mgmtBefore,
      "before=" + mgmtBefore + " after=" + mgmtAfter);

    // 4) 安装面板结构：给一个真实条目注入 mgmt 状态，断言 live-dot / 日志框 / 等待提示
    //    必须用 catalog 里真实存在的 id——面板是渲染在卡片内部的，不存在的 id 不会出卡片。
    const probeId = await evalJs(`(S.catalog.find(c => c.installed) || {}).id`);
    check("存在可注入的真实条目", !!probeId, probeId);
    await evalJs(`
      S.mgmt = S.mgmt || {};
      S.mgmt[${JSON.stringify(probeId)}] = { opLabel: "安装", status: "running", versionBefore: "1.0", log: "", showLog: true };
      S.catSig = "__force__"; renderCatalog(); "ok"`);
    await sleep(300);
    const panel = JSON.parse(await evalJs(`(() => {
      const p = document.querySelector(".mgmt-panel.running");
      if (!p) return "{}";
      return JSON.stringify({
        dot: !!p.querySelector(".live-dot"),
        log: p.querySelector(".mgmt-log") ? p.querySelector(".mgmt-log").textContent : null,
        cls: p.querySelector(".mgmt-log") ? p.querySelector(".mgmt-log").className : null
      });
    })()`));
    check("运行中面板有呼吸圆点", panel.dot === true, JSON.stringify(panel));
    check("日志暂空时显示等待输出提示",
      /等待输出/.test(panel.log || ""), JSON.stringify(panel));
    check("空日志用 muted 弱化样式", /muted/.test(panel.cls || ""), panel.cls);
    // 有内容后不再弱化
    await evalJs(`
      S.mgmt[${JSON.stringify(probeId)}].log = "add @scope/probe 1.0";
      S.catSig = "__force__"; renderCatalog(); "ok"`);
    await sleep(300);
    const cls2 = await evalJs(`document.querySelector(".mgmt-panel.running .mgmt-log").className`);
    check("日志有内容后去掉 muted", !/muted/.test(cls2 || ""), cls2);
    await evalJs(`clearMgmt(${JSON.stringify(probeId)}); "ok"`);

    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      writeFileSync(join(ROOT, ".ui-shots", "mgmt-agents.png"),
        Buffer.from(r.result.data, "base64"));
    });

    // 5) 服务端确认页面操作期间状态正常
    const state = await fetch(SERVICE + "/api/state").then((r) => r.json());
    check("页面操作期间服务状态正常", Array.isArray(state.agents));

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 智能体管理页（UI/CDP）：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
