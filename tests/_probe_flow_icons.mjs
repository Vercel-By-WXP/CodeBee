/* 类型选择器黑白图标探针：Edge headless + CDP（照 ui_check.mjs 模板）。
 * 临时数据目录起服务（端口 18797）→ 验证 13 个内置类型的精灵表图标渲染、
 * 菜单开合/选中同步/pickType 联动、管理弹框图标、外点收起 → 截图 → 清理进程。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18797";
const PORT = 18797;
const CDP_PORT = 9334;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // ---- 起服务（临时数据目录，不碰真实 data/ 与 8765）
  const tmp = mkdtempSync(join(tmpdir(), "tutti-iconprobe-"));
  const pyProc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT },
    cwd: ROOT, stdio: "ignore",
  });
  let svcUp = false;
  for (let i = 0; i < 40 && !svcUp; i++) {
    await sleep(500);
    try {
      const r = await fetch(SERVICE + "/api/state");
      svcUp = r.status === 200;
    } catch (e) { /* 未就绪 */ }
  }
  check("服务启动（临时数据目录 :18797）", svcUp);

  // ---- 后端 flows 自检：13 个内置类型全部带 i-* 图标
  let flows = [];
  try {
    const r = await fetch(SERVICE + "/api/flows");
    flows = (await r.json()).flows || [];
  } catch (e) { /* ignore */ }
  check("内置类型扩到 13 个", flows.length === 13, flows.map((f) => f.id).join(","));
  check("全部类型图标为精灵表 i-* 引用", flows.every((f) => String(f.icon || "").indexOf("i-") === 0),
    JSON.stringify(flows.map((f) => f.icon)));

  // ---- Edge headless + CDP
  const edge = EDGE_CANDIDATES.find(() => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const browser = spawn(edge, [
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
    await sleep(3500);   // 等 load + SSE 首帧 + loadFlows

    // 1) 隐藏 select 仍是值真源（既有测试兼容面）
    const nOpt = await evalJs(`document.getElementById("f-type").options.length`);
    check("隐藏 select 填充 13 项", nOpt === 13, nOpt);
    const selDisp = await evalJs(`getComputedStyle(document.getElementById("f-type")).display`);
    check("select 已隐藏不占版面", selDisp === "none", selDisp);

    // 2) 可见按钮：当前类型名 + svg 图标
    const btnInfo = await evalJs(`(() => {
      const b = document.getElementById("f-type-btn");
      const r = b.getBoundingClientRect();
      const use = b.querySelector("use");
      return { w: Math.round(r.width), h: Math.round(r.height),
        name: document.getElementById("f-type-name").textContent,
        href: use ? use.getAttribute("href") : null };
    })()`);
    check("类型按钮可见（有尺寸）", btnInfo.w > 80 && btnInfo.h > 30, JSON.stringify(btnInfo));
    check("按钮显示当前类型名（代码）", btnInfo.name === "代码", btnInfo.name);
    check("按钮图标 href=i-code", btnInfo.href === "#i-code", btnInfo.href);

    // 3) 打开菜单：13 行、每行图标 href 均有对应 symbol、选中行高亮
    await evalJs(`toggleTypeMenu(); "ok"`);
    await sleep(200);
    const menuInfo = await evalJs(`(() => {
      const m = document.getElementById("type-menu");
      const items = [...m.querySelectorAll(".type-item")];
      const missing = [];
      for (const it of items) {
        const href = it.querySelector("use")?.getAttribute("href") || "";
        if (!href.startsWith("#i-") || !document.querySelector("#icon-sprite " + href.replace("#", "#i-").replace("#i-i-", "#i-")) && !document.getElementById(href.slice(1))) missing.push(href);
      }
      const on = items.find((i) => i.classList.contains("on"));
      const first = items[0];
      const ico = first.querySelector(".ti-ico svg");
      return { visible: !m.classList.contains("hidden"), n: items.length, missing,
        onV: on ? on.dataset.v : null,
        checkVisible: on ? getComputedStyle(on.querySelector(".ti-check")).visibility : null,
        stroke: ico ? getComputedStyle(ico).stroke : null,
        icoBox: ico ? (() => { const r = ico.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })() : null };
    })()`);
    check("菜单展开可见", menuInfo.visible === true);
    check("菜单 13 行类型", menuInfo.n === 13, menuInfo.n);
    check("全部行图标 href 都能在精灵表解析", menuInfo.missing.length === 0, JSON.stringify(menuInfo.missing));
    check("当前行高亮（.on=code）", menuInfo.onV === "code", menuInfo.onV);
    check("选中行对勾可见", menuInfo.checkVisible === "visible", menuInfo.checkVisible);
    check("图标为单色描边（stroke=currentColor 有值）", !!menuInfo.stroke && menuInfo.stroke !== "none", menuInfo.stroke);
    check("图标几何 17×17", JSON.stringify(menuInfo.icoBox) === "[17,17]", JSON.stringify(menuInfo.icoBox));

    // 截图：展开态
    mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
    const shot1 = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r4-type-menu.png"), Buffer.from(shot1.result.data, "base64"));

    // 4) pickType 联动：切 novel → 隐藏 select/按钮/评审表单区同步
    await evalJs(`pickType("novel"); "ok"`);
    await sleep(300);
    const afterNovel = await evalJs(`(() => ({
      v: document.getElementById("f-type").value,
      name: document.getElementById("f-type-name").textContent,
      href: document.querySelector("#f-type-ico use")?.getAttribute("href"),
      reviewShown: !document.getElementById("f-review-only").classList.contains("hidden"),
      rubric: document.getElementById("f-rubric").value,
      menuClosed: document.getElementById("type-menu").classList.contains("hidden") }))()`);
    check("pickType(novel) → select 值切换", afterNovel.v === "novel", JSON.stringify(afterNovel));
    check("按钮名同步（小说）", afterNovel.name === "小说", afterNovel.name);
    check("按钮图标同步 i-book-open", afterNovel.href === "#i-book-open", afterNovel.href);
    check("评审表单区显示 + 维度预填", afterNovel.reviewShown === true && /情节/.test(afterNovel.rubric || ""), afterNovel.rubric);
    check("选择后菜单自动收起", afterNovel.menuClosed === true);

    // 5) 新类型可用：video_script（短视频脚本）
    await evalJs(`pickType("video_script"); "ok"`);
    await sleep(300);
    const afterVs = await evalJs(`(() => ({
      v: document.getElementById("f-type").value,
      ms: document.getElementById("f-manuscript").value,
      href: document.querySelector("#f-type-ico use")?.getAttribute("href") }))()`);
    check("新增类型可选（video_script）", afterVs.v === "video_script", JSON.stringify(afterVs));
    check("新类型产出文件名带出 script.md", afterVs.ms === "script.md", afterVs.ms);
    check("新类型图标 i-clapper", afterVs.href === "#i-clapper", afterVs.href);

    // 6) 外点收起
    await evalJs(`toggleTypeMenu(true); "ok"`);
    await sleep(100);
    await evalJs(`document.body.click(); "ok"`);
    await sleep(150);
    const closedByOutside = await evalJs(`document.getElementById("type-menu").classList.contains("hidden")`);
    check("点击外部收起菜单", closedByOutside === true);

    // 7) 流程管理弹框：行内也是 svg 图标
    await evalJs(`openFlowsManager(); "ok"`);
    await sleep(300);
    const mgr = await evalJs(`(() => {
      const names = [...document.querySelectorAll("#modal .item .name")];
      return { rows: names.length,
        svgRows: names.filter((n) => n.querySelector("svg use[href^='#i-']")).length };
    })()`);
    check("管理弹框 13 行且行行带 svg 图标", mgr.rows === 13 && mgr.svgRows === 13, JSON.stringify(mgr));
    await evalJs(`closeModal(); "ok"`);

    // 截图：收起态（novel 选中）
    const shot2 = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(ROOT, ".ui-shots", "r4-type-closed.png"), Buffer.from(shot2.result.data, "base64"));
  } finally {
    browser.kill();
    pyProc.kill();
    await sleep(500);
    if (results.every((r) => r.ok)) rmSync(tmp, { recursive: true, force: true });
    else console.log("（失败现场保留：%s）", tmp);
  }

  const fail = results.filter((r) => !r.ok).length;
  console.log("\n===== 类型图标探针：%d 通过 / %d 失败 =====", results.length - fail, fail);
  if (fail) process.exit(1);
}

main().catch((e) => { console.error("探针异常：", e); process.exit(1); });
