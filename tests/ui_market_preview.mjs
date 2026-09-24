/* 市场外部目录「先看后装」两阶段安装 UI 回归：
 * stub 掉全部 /api/market/remote* 请求（不打真网）→ 进插件市场外部视图 →
 * 点安装 → 断言预览弹框走通用骨架（title/body/foot）、列文件清单、点名剥离项、
 * 完整性说明如实 → 确认安装 → 断言安装 POST 带 token、弹框关闭。
 * 顺带断言「已安装 N」过滤入口：计数、aria-pressed、请求带 installed=1。
 * 浏览器段走 _ui_boot.mjs（DPR 钉 1、CDP 随机口、环境回读进结果头）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { bootEdge } from "./_ui_boot.mjs";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18961;
const SERVICE = "http://127.0.0.1:" + PORT;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* 页内 fetch stub：外部目录三端点全接管，其余透传真服务 */
const STUB_JS = `(() => {
  window.__mktPosts = [];
  window.__mktGets = [];
  const orig = window.fetch.bind(window);
  const json = (obj) => new Response(JSON.stringify(obj),
    { status: 200, headers: { "Content-Type": "application/json" } });
  window.fetch = (url, opts) => {
    const u = String(url);
    if (u.indexOf("/api/market/remote/preview") >= 0) {
      window.__mktPosts.push({ url: u, body: (opts && opts.body) || "" });
      return Promise.resolve(json({
        token: "tok123", id: "remote-zcode-Demo-Skill", title: "Demo Skill",
        source: "ZCode 官方", version: "1.0", integrity: "unverified",
        skills: ["Demo Skill"], files_total: 3, total_chars: 4200,
        file_list: [
          { path: "market-remote-zcode-Demo-Skill.md", chars: 3600 },
          { path: "demo-skill/refs.md", chars: 600 },
        ],
        stripped: ["scripts/setup.py", "hooks/hook.js"], stripped_total: 2, expires_in: 600,
      }));
    }
    if (u.indexOf("/api/market/remote/install") >= 0) {
      window.__mktPosts.push({ url: u, body: (opts && opts.body) || "" });
      return Promise.resolve(json({ ok: true, skills: 1, stripped: ["scripts/setup.py"] }));
    }
    if (u.indexOf("/api/market/remote") >= 0) {
      window.__mktGets.push(u);
      return Promise.resolve(json({
        sources: [{ id: "zcode", name: "ZCode 官方", fetched_at: "2026-09-24 00:00:00", count: 1 }],
        entries: [{
          id: "remote-zcode-Demo-Skill", name: "Demo Skill", title: "Demo Skill",
          desc: "demo pack", source_id: "zcode", source_name: "ZCode 官方", version: "1.0",
          category: "", keywords: [], install: { kind: "zip" }, compat: "ok",
          block_reason: "", installed: false, installable: true,
        }],
        total: 1, offset: 0, has_more: false, categories: [], installed_total: 0,
      }));
    }
    return orig(url, opts);
  };
  return "ok";
})()`;

async function main() {
  const dataDir = join(mkdtempSync(join(tmpdir(), "tutti-mkp-")), "data");
  mkdirSync(dataDir, { recursive: true });
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"],
    { cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT } });
  const { send, evalJs, envInfo, close } = await bootEdge({ width: 1400, height: 950 });
  console.log("  · " + envInfo.line);
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state").then((r) => r.status)) === 200; } catch (e) {}
    }
    check("测试服务启动（TUTTI_DATA 临时目录）", up);
    if (!up) return;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);   // 等 boot + SSE 首帧
    check("DPR 钉 1", (await evalJs("window.devicePixelRatio")) === 1);
    await evalJs(STUB_JS);
    await evalJs(`switchTab("market"); "ok"`);
    await sleep(700);
    await evalJs(`mkSetView("remote"); "ok"`);
    await sleep(700);

    // 外部目录渲染 + 「已安装 N」入口
    check("外部目录条目渲染",
      !!(await evalJs(`document.querySelector('#mkr-grid button[data-mk="remote-zcode-Demo-Skill"]')`)));
    const instTxt = await evalJs(`document.getElementById("mkr-installed").textContent`);
    check("「已安装 N」入口带全量计数", /已安装\s*0/.test(instTxt || ""), instTxt);

    // 两阶段安装：预览弹框
    await evalJs(`mkrInstall("remote-zcode-Demo-Skill"); "ok"`);
    await sleep(700);
    check("预览弹框打开", !(await evalJs(`document.getElementById("modal").classList.contains("hidden")`)));
    const title = await evalJs(`document.getElementById("modal-title").textContent`);
    check("弹框走通用骨架且标题=安装前确认", (title || "").indexOf("安装前确认") >= 0, title);
    const bodyHtml = await evalJs(`document.getElementById("modal-body").innerHTML`) || "";
    check("弹框列出文件清单", bodyHtml.indexOf("market-remote-zcode-Demo-Skill.md") >= 0
      && bodyHtml.indexOf("demo-skill/refs.md") >= 0);
    check("弹框点名剥离项（不写入、不执行）", bodyHtml.indexOf("scripts/setup.py") >= 0
      && bodyHtml.indexOf("将自动剥离") >= 0);
    check("完整性说明如实（无哈希兜底口径）", bodyHtml.indexOf("无哈希") >= 0);
    check("弹框含技能与体量汇总", bodyHtml.indexOf("Demo Skill") >= 0 && bodyHtml.indexOf("3") >= 0);
    const foot = await evalJs(`document.getElementById("modal-foot").textContent`) || "";
    check("底部钉住 取消+确认安装", foot.indexOf("取消") >= 0 && foot.indexOf("确认安装") >= 0, foot);

    // 确认安装：POST 带 token
    await evalJs(`document.getElementById("mkpv-ok").click(); "ok"`);
    await sleep(700);
    check("确认后弹框关闭", !!(await evalJs(`document.getElementById("modal").classList.contains("hidden")`)));
    const posts = await evalJs(`JSON.stringify(window.__mktPosts)`);
    const list = JSON.parse(posts || "[]");
    const inst = list.find((p) => p.url.indexOf("/install") >= 0);
    check("安装 POST 已发出且带 token",
      !!inst && JSON.parse(inst.body).token === "tok123", posts);
    check("调用顺序=先预览后安装", list.length === 2 && list[0].url.indexOf("preview") >= 0, posts);

    // 「已安装 N」过滤开关：请求带 installed=1 + aria-pressed
    await evalJs(`document.getElementById("mkr-installed").click(); "ok"`);
    await sleep(600);
    const gets = JSON.parse((await evalJs(`JSON.stringify(window.__mktGets)`)) || "[]");
    check("过滤开启后请求带 installed=1", (gets[gets.length - 1] || "").indexOf("installed=1") >= 0, JSON.stringify(gets));
    const pressed = await evalJs(`document.getElementById("mkr-installed").getAttribute("aria-pressed")`);
    check("过滤入口 aria-pressed=true", pressed === "true", pressed);
  } finally {
    await close();
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(600);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 市场预览安装（UI）：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
