/* 代码设置（皮肤页）无头验证：Edge headless + CDP。
 * 1) 设置区与预览卡渲染：8 主题下拉、开关初值、双卡「当前生效」徽章。
 * 2) 交互链：换深色主题→localStorage 落键→预览卡 data-codetheme 变→当前生效徽章随明暗。
 * 3) 行号开关：html.code-linenum 类切换、cb-ln 显示态。
 * 4) 换行开关：html.code-wrap 类切换。
 * 5) 字号：落 --code-size，钳制 10–22。
 * 6) 文件弹窗：artPopup 代码文件走 codeBlockHTML（.code-block 表格 + 高亮 span + 行号）。
 * 7) diff 弹窗：_fpDiffLine 行号槽受 code-linenum 控制。
 * 用法：node tests/ui_code_settings.mjs （脚本自己起临时服务，端口 18813） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18813;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9343;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-codeset-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
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
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result && r.result.exceptionDetails) throw new Error("页面报错: " + JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result && r.result.result ? r.result.result.value : undefined;
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    // 让页面拿到控制权（写接口 423 防线），并自动打掉 alert（evalJs 才不挂死）
    await send("Page.addScriptToEvaluateOnNewDocument", {
      source: `window.confirm=()=>true; window.alert=()=>{}; window.prompt=()=>'';
        setInterval(()=>{ try{ localStorage.setItem('orch.deviceCtl','1'); }catch(e){} }, 1000);`,
    });

    // ── 进皮肤页（设置导航）──
    await evalJs(`(function(){
      document.body.classList.remove("side-collapsed");
      const gear=document.getElementById("btn-gear")||document.querySelector('[data-tab="appearance"],#nav-appearance');
      if(gear) gear.click();
      if(typeof switchTab==="function") switchTab("appearance");
      return true;
    })()`);
    await sleep(900);

    // ── 1. 设置区渲染 ──
    check("代码设置面板存在", await evalJs(`!document.getElementById("cs-theme-light") ? false : true`));
    check("浅色下拉有 4 个主题", await evalJs(`document.getElementById("cs-theme-light").options.length===4`));
    check("深色下拉有 4 个主题", await evalJs(`document.getElementById("cs-theme-dark").options.length===4`));
    check("默认浅色=github(GitHub Light)", await evalJs(`document.getElementById("cs-theme-light").value==="github"`));
    check("默认深色=github-dark(GitHub Dark)", await evalJs(`document.getElementById("cs-theme-dark").value==="github-dark"`));
    check("行号开关默认开", await evalJs(`document.getElementById("cs-linenum").checked===true`));
    check("换行开关默认关", await evalJs(`document.getElementById("cs-wrap").checked===false`));
    check("字号默认 12.5", await evalJs(`document.getElementById("cs-size").value==="12.5"`));

    // ── 2. 预览卡与「当前生效」徽章（默认夜间）──
    check("双预览卡均渲染 .code-block", await evalJs(`document.querySelectorAll(".cs-pv-code .code-block").length===2`));
    check("预览卡浅色=github / 深色=github-dark", await evalJs(
      `document.querySelector('#cs-preview-light .cs-pv-code').dataset.codetheme==="github" &&
       document.querySelector('#cs-preview-dark .cs-pv-code').dataset.codetheme==="github-dark"`));
    const badgeLight = await evalJs(`document.querySelector('#cs-preview-light .cs-pv-badge').textContent`);
    const badgeDark = await evalJs(`document.querySelector('#cs-preview-dark .cs-pv-badge').textContent`);
    // 默认明暗是夜间 → 深色卡标「当前生效」
    check("夜间下深色卡标「当前生效」", badgeDark === "当前生效" && badgeLight !== "当前生效",
      "light=" + badgeLight + " dark=" + badgeDark);

    // ── 3. 换深色主题 → localStorage + 预览卡联动 ──
    await evalJs(`(function(){
      const s=document.getElementById("cs-theme-dark"); s.value="monokai";
      s.dispatchEvent(new Event("change",{bubbles:true}));
      return true;
    })()`);
    await sleep(300);
    check("深色换 Monokai 后预览卡 data-codetheme 联动", await evalJs(
      `document.querySelector('#cs-preview-dark .cs-pv-code').dataset.codetheme==="monokai"`));
    check("localStorage 落键 orch.codeTheme.dark=monokai", await evalJs(
      `localStorage.getItem("orch.codeTheme.dark")==="monokai"`));
    check("html[data-codetheme] 同步为 monokai（夜间生效链）", await evalJs(
      `document.documentElement.dataset.codetheme==="monokai"`));
    check("预览代码含高亮 span（.ct-kw 存在）", await evalJs(
      `document.querySelectorAll(".cs-pv-code .ct-kw").length>0`));

    // ── 4. 行号开关 ──
    check("行号开着时 cb-ln 单元格可见", await evalJs(
      `getComputedStyle(document.querySelector(".cs-pv-code .cb-ln")).display!=="none"`));
    await evalJs(`(function(){
      const c=document.getElementById("cs-linenum"); c.checked=false;
      c.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(300);
    check("关行号后 html.code-linenum 移除 + cb-ln 隐藏", await evalJs(
      `!document.documentElement.classList.contains("code-linenum") &&
       getComputedStyle(document.querySelector(".cs-pv-code .cb-ln")).display==="none"`));
    check("localStorage orch.code.linenum=0", await evalJs(`localStorage.getItem("orch.code.linenum")==="0"`));
    await evalJs(`(function(){
      const c=document.getElementById("cs-linenum"); c.checked=true;
      c.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(200);

    // ── 5. 换行开关 ──
    check("换行默认关：cb-code 为 pre", await evalJs(
      `!document.documentElement.classList.contains("code-wrap") &&
       getComputedStyle(document.querySelector(".cs-pv-code .cb-code")).whiteSpace==="pre"`));
    await evalJs(`(function(){
      const c=document.getElementById("cs-wrap"); c.checked=true;
      c.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(300);
    check("开换行后 cb-code 为 pre-wrap", await evalJs(
      `document.documentElement.classList.contains("code-wrap") &&
       getComputedStyle(document.querySelector(".cs-pv-code .cb-code")).whiteSpace==="pre-wrap"`));
    await evalJs(`(function(){
      const c=document.getElementById("cs-wrap"); c.checked=false;
      c.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(200);

    // ── 6. 字号与钳制 ──
    await evalJs(`(function(){
      const i=document.getElementById("cs-size"); i.value="15";
      i.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(300);
    check("字号 15 → --code-size=15px", await evalJs(
      `document.documentElement.style.getPropertyValue("--code-size")==="15px"`));
    await evalJs(`(function(){
      const i=document.getElementById("cs-size"); i.value="99";
      i.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(300);
    check("字号 99 被钳到 22", await evalJs(`localStorage.getItem("orch.code.size")==="22"`));
    // 还原默认字号，避免影响后续用例
    await evalJs(`(function(){
      const i=document.getElementById("cs-size"); i.value="12.5";
      i.dispatchEvent(new Event("input",{bubbles:true}));
      return true;
    })()`);
    await sleep(200);

    // ── 7. 文件弹窗：代码文件走 codeBlockHTML ──
    const fp = await evalJs(`(function(){
      if (typeof codeBlockHTML!=="function") return "no-fn";
      const html=codeBlockHTML('const a = 42 + "x"; // demo');
      return {
        table: html.indexOf('class="code-block"')>=0,
        ln: html.indexOf('class="cb-ln"')>=0,
        kw: html.indexOf('ct-kw')>=0,
        num: html.indexOf('ct-num')>=0,
        com: html.indexOf('ct-com')>=0,
        str: html.indexOf('ct-str')>=0,
      };
    })()`);
    check("codeBlockHTML：表格+行号+关键词/数字/注释/字符串高亮",
      typeof fp === "object" && fp.table && fp.ln && fp.kw && fp.num && fp.com && fp.str, JSON.stringify(fp));

    // 直接调 artPopup 内部分流不可行（需真实文件），改为验证 fp-code 渲染管线已被替换：
    // artPopup 抽出共用的 _fpPreviewUrl（成品弹窗与目录文件弹窗共用），分流代码在那里面
    check("artPopup 代码分支用 codeBlockHTML（源码核验）", await evalJs(
      `String(artPopup).indexOf("_fpPreviewUrl")>=0 && String(_fpPreviewUrl).indexOf("codeBlockHTML")>=0`));
    check("fpDiffPopup 行号管线（_fpDiffLine 带 no 槽）", await evalJs(
      `String(_fpDiffLine).indexOf("fp-no")>=0 && String(_fpDiffLine).indexOf("codeLineNum")>=0`));

    // ── 8. 明暗切换 → 徽章挪卡 + 主题跟随 ──
    await evalJs(`(function(){
      if(typeof setThemeMode==="function") setThemeMode("light");
      return true;
    })()`);
    await sleep(400);
    const badgeLight2 = await evalJs(`document.querySelector('#cs-preview-light .cs-pv-badge').textContent`);
    check("切日间后「当前生效」挪到浅色卡", badgeLight2 === "当前生效", "light=" + badgeLight2);
    check("日间下 html[data-codetheme]=github（不受深色改 Monokai 影响）", await evalJs(
      `document.documentElement.dataset.codetheme==="github"`));
    await evalJs(`(function(){ if(typeof setThemeMode==="function") setThemeMode("dark"); return true; })()`);
    await sleep(200);

    // ── 8.5 页内锚点：胶囊点击滚动 + 高亮跟随 ──
    check("锚点胶囊存在且初亮「外观」", await evalJs(
      `document.querySelectorAll("#cs-anchor [data-apn]").length===2 &&
       document.querySelector("#cs-anchor [data-apn='apn-skin']").classList.contains("active")`));
    await evalJs(`(function(){
      const b=document.querySelector("#cs-anchor [data-apn='apn-code']");
      b.click(); return true;
    })()`);
    await sleep(900);   // 平滑滚动 + 落定归位（apnScrollTo 内 600ms 后 apnSyncActive）
    check("点「代码」后面板滚进视口（top 接近容器顶）", await evalJs(
      `Math.abs(document.getElementById("apn-code").getBoundingClientRect().top
        - document.querySelector("main").getBoundingClientRect().top) < 160`));
    check("落定后高亮归到「代码」胶囊", await evalJs(
      `document.querySelector("#cs-anchor [data-apn='apn-code']").classList.contains("active")`));
    // 反向：滚回顶部，「外观」亮回
    await evalJs(`document.querySelector("main").scrollTo({top:0}); true`);
    await sleep(500);
    check("滚回顶部后「外观」胶囊亮回", await evalJs(
      `document.querySelector("#cs-anchor [data-apn='apn-skin']").classList.contains("active")`));

    // ── 8.7 成品预览链：点文件与「预览」按钮同款 artPopup 弹窗（面板内嵌预览已移除）──
    check("artPopup 的 md 分支统一 codeBlockHTML（无 fp-md 代码路径）", await evalJs(
      `String(_fpPreviewUrl).indexOf('codeBlockHTML(text)')>=0 && String(_fpPreviewUrl).indexOf("fp-md")<0`));
    check("预览按钮扩到全部文本类文件（FP_TXT 判定而非 md/txt 正则）", await evalJs(
      `String(artifactsChips).indexOf("FP_TXT")>=0 && String(artifactsChips).indexOf("isMd")<0`));
    check("预览按钮改走 artPopup 弹窗，面板内嵌预览已整体移除", await evalJs(
      `String(artifactsChips).indexOf("artPopup")>=0 &&
       String(artifactsChips).indexOf("previewArtifact")<0 &&
       typeof window.previewArtifact==="undefined" &&
       !document.getElementById("rd-preview")`));

    // ── 9. 英文模式：新词条走 i18n 字典（走真实切换入口 setLangBtn，动态徽章才会重画）──
    await evalJs(`(function(){
      if (typeof setLangBtn==="function") { setLangBtn("en"); return true; }
      localStorage.setItem("orch.lang","en");
      if (typeof applyI18n==="function") applyI18n();
      return false;
    })()`);
    await sleep(500);
    check("英文模式：面板标题 → Code Display", await evalJs(
      `document.querySelector("#apn-code h2").textContent==="Code Display"`),
      await evalJs(`document.querySelector("#apn-code h2").textContent`));
    check("英文模式：显示行号 → Show line numbers", await evalJs(
      `document.querySelector("#cs-row-linenum, .cs-row:nth-child(3) b").textContent==="Show line numbers"`));
    check("英文模式：预览徽章 → Active", await evalJs(
      `document.querySelector('#cs-preview-dark .cs-pv-badge').textContent==="Active"`));
  } catch (e) {
    check("用例执行无异常", false, e.message);
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    if (edge) { try { edge.kill(); } catch (e) { /* ignore */ } }
    if (svc) { try { svc.kill(); } catch (e) { /* ignore */ } }
    await sleep(600);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const failed = results.filter((r) => !r.ok);
  console.log("\n==== 结果: " + (results.length - failed.length) + "/" + results.length + " 通过 ====");
  if (failed.length) process.exit(1);
}

main().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
