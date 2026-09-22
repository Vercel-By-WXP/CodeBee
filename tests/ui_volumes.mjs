/* 分卷 UI（浏览器内）：新建表单分卷输入 + 章节卡按卷分组 + 发布页按卷列章。
 * 直接以假 run/假 task 调渲染函数断言 DOM——不造任务/不起编排，稳且快。
 * 端口/CDP 口都随 pid 偏移（并行代理跑测试不会双绑互驱，见 memory）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 19300 + (process.pid % 300);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9900 + (process.pid % 400);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-volui-"));
  writeFileSync(join(dataDir, "last-prefs.json"), JSON.stringify({ type: "serial_novel" }), "utf8");
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-volcdp-"));
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
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 就绪", !!target);
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
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) {
        throw new Error(r.result.exceptionDetails.exception?.description ||
          r.result.exceptionDetails.text || "browser evaluation failed");
      }
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    // ---- 1) 新建表单：分卷字段存在，连载类型下预填每卷章数 ----
    // 类型下拉的 option 由 /api/state 的 flows 渲染，先等它铺好再选类型
    for (let i = 0; i < 20; i++) {
      if ((await evalJs(`document.getElementById("f-type").options.length`)) > 1) break;
      await sleep(500);
    }
    await evalJs(`pickType("serial_novel")`);
    await sleep(900);
    const form = JSON.parse(await evalJs(`JSON.stringify({
      per: document.getElementById("f-vol-chapters") ? document.getElementById("f-vol-chapters").value : null,
      spec: document.getElementById("f-vol-spec") ? document.getElementById("f-vol-spec").value : null,
      specExists: !!document.getElementById("f-vol-spec"),
      perExists: !!document.getElementById("f-vol-chapters"),
      typeVal: document.getElementById("f-type").value
    })`));
    check("分卷「每卷章数」字段存在", form.perExists === true, JSON.stringify(form));
    check("分卷「指定卷结构」字段存在", form.specExists === true, JSON.stringify(form));
    check("连载类型下每卷章数已预填（开箱即用）", form.typeVal === "serial_novel" && form.per === "20",
      JSON.stringify(form));

    // ---- 1b) 真实提交路径：填卷结构文本 → 建任务 → 回读 serial（端到端接线）----
    // 工作目录直接写入隐藏真源（原生目录选择框属另一测试范围）。
    const wd = mkdtempSync(join(tmpdir(), "tutti-volwd-"));
    await evalJs(`
      (() => {
        document.getElementById("f-goal").value = "写一部短篇连载，供分卷 UI 测试";
        document.getElementById("f-workdir").value = ${JSON.stringify(wd)};
        document.getElementById("f-vol-spec").value = "第一卷 少年初入江湖 20章；第二卷 风云再起 16章";
        return true;
      })()`);
    await evalJs(`document.getElementById("btn-create").click()`);
    let created = null;
    for (let i = 0; i < 24 && !created; i++) {
      await sleep(500);
      try {
        const st = await (await fetch(SERVICE + "/api/state")).json();
        created = (st.tasks || []).find((x) => (x.goal || "").includes("分卷 UI 测试")) || null;
      } catch (e) { /* 未就绪 */ }
    }
    check("UI 提交建出任务", !!created, JSON.stringify(created && created.id));
    const vols = (created && created.serial && created.serial.volumes) || [];
    check("表单卷结构文本被后端解析成卷表（端到端）",
      vols.length === 2 && vols[0].title === "少年初入江湖" && vols[1].title === "风云再起",
      JSON.stringify(vols));
    // 工作目录可能被刚建的任务占用（Windows 文件锁），清理失败不算测试失败
    try { rmSync(wd, { recursive: true, force: true }); } catch (e) { /* 忽略 */ }

    // ---- 2) 章节卡按卷分组 ----
    const grouped = JSON.parse(await evalJs(`
      (() => {
        renderChapterScores({
          volumes: [{ vol: 1, title: "少年初入江湖", first: 1, last: 2 },
                    { vol: 2, title: "风云再起", first: 3, last: 4 }],
          chapter_scores: [
            { chapter: 1, title: "开篇", means: { "情节": 8 }, passed: true, words: 2200, vol: 1, vol_title: "少年初入江湖" },
            { chapter: 2, title: "试炼", means: { "情节": 7 }, passed: true, words: 2300, vol: 1, vol_title: "少年初入江湖" },
            { chapter: 3, title: "遇袭", means: { "情节": 6 }, passed: false, words: 2100, vol: 2, vol_title: "风云再起" },
            { chapter: 4, title: "反击", means: { "情节": 8 }, passed: true, words: 2400, vol: 2, vol_title: "风云再起" }
          ]
        });
        const box = document.getElementById("rd-chapters");
        return JSON.stringify({
          visible: !box.classList.contains("hidden"),
          nVols: box.querySelectorAll(".ch-vol").length,
          nRows: box.querySelectorAll(".ch-row").length,
          heads: Array.from(box.querySelectorAll(".ch-vol-head b")).map((e) => e.textContent),
          text: box.textContent
        });
      })()`));
    check("分卷章节卡分组渲染两卷", grouped.visible && grouped.nVols === 2 && grouped.nRows === 4, JSON.stringify(grouped));
    check("卷头带卷号与卷名", grouped.heads[0].includes("少年初入江湖") && grouped.heads[1].includes("风云再起"), JSON.stringify(grouped.heads));
    check("卷内达标数徽标渲染", /2\/3/.test(grouped.text) || /1\/2/.test(grouped.text), grouped.text.slice(0, 200));

    // ---- 3) 不分卷：退化为平铺（不出现 .ch-vol），视觉零变化 ----
    const flat = JSON.parse(await evalJs(`
      (() => {
        renderChapterScores({ chapter_scores: [
          { chapter: 1, title: "A", means: { "情节": 8 }, passed: true, words: 100 },
          { chapter: 2, title: "B", means: { "情节": 9 }, passed: true, words: 120 }
        ] });
        const box = document.getElementById("rd-chapters");
        return JSON.stringify({ nVols: box.querySelectorAll(".ch-vol").length, nRows: box.querySelectorAll(".ch-row").length });
      })()`));
    check("不分卷退化为平铺列表（无卷头）", flat.nVols === 0 && flat.nRows === 2, JSON.stringify(flat));

    // ---- 4) 发布页按卷分组列章（含 fn 直接调用 + 假 task） ----
    const pub = JSON.parse(await evalJs(`
      (() => {
        const plan = volumePlanOf({ serial: { volume_chapters: 2 } }, 4);
        const spec = volumePlanOf({ serial: { volumes: [{ title: "卷一 起", chapters: 2 }, { title: "卷二 承", chapters: 2 }] } }, 4);
        const none = volumePlanOf({ serial: {} });
        return JSON.stringify({ plan: plan, spec: spec, none: none });
      })()`));
    check("前端卷规划表：每卷章数铺到末章", pub.plan.length === 2 && pub.plan[1].first === 3, JSON.stringify(pub.plan));
    check("前端卷规划表：显式卷表带卷名", pub.spec.length === 2 && pub.spec[0].title === "卷一 起", JSON.stringify(pub.spec));
    check("不分卷返回空表（调用方退化平铺）", pub.none.length === 0, JSON.stringify(pub.none));

    // ---- 5) 英文态新词条不空 ----
    const en = JSON.parse(await evalJs(`
      (() => {
        window.setLang && window.setLang("en");
        const s = t("分卷：每卷章数（空 = 不分卷）");
        window.setLang && window.setLang("zh");
        return JSON.stringify({ s: s });
      })()`));
    check("英文词条命中（非中文回落）", en.s && !/[\u4e00-\u9fa5]/.test(en.s), en.s);
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }

  const fails = results.filter((r) => !r.ok);
  console.log(fails.length ? `\n${fails.length}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
