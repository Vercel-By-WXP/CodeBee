/* 作品信息「换名」按钮验收：Edge headless + CDP，零依赖（ui_bookmeta 同族）。
 * 造数：done 连载任务 + 两平台 book_meta 直接种盘（不等生成线程）→
 * 断言：done 态书名行出「换名」钮（data-task/plat/old 齐）/ stub fetch 拦
 * rename 接口：点击立即禁用+「换名中…」+ 请求 URL/body 正确 → resolve 后
 * toast「已换名」含新书名 + 按钮恢复可点（S.bmRenaming 跨重绘态正确归位）/
 * 失败路径：stub 回 400 → toast 报错 + 面板书名与其他字段原样保留。
 * 回填链路（SSE/轮询拉真数据）由 e2e_bookmeta 服务端断言覆盖；本探针 stub 下
 * 面板书名保持种盘值是预期，不是 bug。
 * 临时数据目录 + 独立端口（18845 / CDP 9391，与其他 UI 测试互不冲撞）。
 * 用法：node tests/ui_bookmeta_rename.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18845;
const CDP_PORT = 9391;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990104-000000-0006";
const TASK_ID = "rn-t1";
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

const FQ_DATA = {
  book_name: "戍边骑奴", signing_mode: "连载模式", target_reader: "男频",
  category: "历史古代", tags_theme: ["历史脑洞"], tags_role: ["强者"],
  tags_plot: ["逆袭"], content_plot: ["扮猪吃虎"], content_emotion: ["热血"],
  content_character: ["杀伐果断"], content_world: ["架空"],
  protagonist_1: "陈砚", protagonist_2: "", summary: "少年戍边，以军功改命，从骑奴到将军。",
};

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-bmrn-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(runsDir, "steps", "01-outline-orch.log"), "--- 输出 ---\n大纲 10 章\n", "utf-8");
  writeFileSync(join(workdir, "chapter-001.md"), "# 第一章\n\n开局即冲突。\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", TASK_ID + ".json"), JSON.stringify({
    id: TASK_ID, title: "换名验收", type: "serial_novel",
    goal: "男频军事文：少年戍边以军功改命", workdir, status: "done",
    serial: { chapters: 10, words_per_chapter: 2000, start_chapter: 1 },
    book_meta: {
      fanqie: { status: "done", data: FQ_DATA, source: "编排者(假·glm-x)",
        at: "2026-10-02 00:00:00", file: "作品信息-番茄.md" },
      qimao: { status: "done", data: { ...FQ_DATA, book_name: "戍边骑奴" },
        source: "编排者(假·glm-x)", at: "2026-10-02 00:01:00", file: "作品信息-七猫.md" },
    },
    created_at: "2000-01-04 00:00:00", mode: "manual", archived: false,
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "换名验收", task_id: TASK_ID,
    status: "done", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "orch", agent_label: "编排者", note: "", status: "done",
        started_at: "00:00:01", ended_at: "00:00:20", duration_s: 19, exit_code: 0,
        summary: "大纲来源 编排者：10 章", log: "steps/01-outline-orch.log", cost_usd: 0, tokens: 0 },
    ],
    outline: { book_title: "戍边骑奴", chapters: [
      { title: "开局", beats: "骑奴陈砚夜杀蛮族斥候", hook: "边关的雪没过脚踝" },
    ] },
    created_at: "2000-01-04 00:00:00", started_at: "2000-01-04 00:00:00",
    ended_at: "2000-01-04 00:02:00", cost_usd: 0, tokens: 0, error: "",
    verdict: null, summary: "",
  }, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18845)", up);

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
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
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

    // 打开详情 → 切作品信息 TAB
    const open = await evalJson(`(async () => {
      sideOpenTask("${TASK_ID}");
      await new Promise((r) => setTimeout(r, 1200));
      const tb = document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]');
      if (tb) tb.click();
      await new Promise((r) => setTimeout(r, 400));
      return "ok";
    })()`);
    check("详情打开且作品信息 TAB 可切", open === "ok", JSON.stringify(open));

    // stub fetch（导航后注入）：只拦 rename 口。第 1 次成功（延迟 600ms 造
    // 「换名中」断言窗口），第 2 次 400 撞名；其余全走真后端
    await evalJson(`(window.__rnCalls=[]); (window.__rnN=0); (function(){
      window.alert = () => true; window.confirm = () => true;
      const orig = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        const method = String((opts || {}).method || "GET").toUpperCase();
        if (u.includes("/book-meta/rename") && method === "POST") {
          const n = ++window.__rnN;
          window.__rnCalls.push({ url: u, body: (opts.body || ""), n });
          if (n === 1) {
            await new Promise((r) => setTimeout(r, 600));
            return new Response(JSON.stringify({ ok: true, old: "戍边骑奴", new: "烽燧月" }),
              { status: 200, headers: { "Content-Type": "application/json" } });
          }
          return new Response(JSON.stringify({ error: "新书名《戍边骑奴》仍与本站已有作品重名" }),
            { status: 400, headers: { "Content-Type": "application/json" } });
        }
        return orig(url, opts);
      };
    })(); true`);
    check("stub fetch 注入", true);

    // A) done 态书名行出「换名」钮：两平台各一，属性齐
    const btns = await evalJson(`(() => {
      const box = document.getElementById("rd-bookmeta");
      const bs = Array.from(box.querySelectorAll('.bm-f button[onclick="bmRename(this)"]'));
      return bs.map((b) => ({ task: b.dataset.task, plat: b.dataset.plat,
        old: b.dataset.old, text: b.textContent.trim(), disabled: b.disabled }));
    })()`);
    check("两平台卡书名行各一个「换名」钮",
      btns.length === 2 && btns.every((b) => b.task === TASK_ID && !b.disabled
        && b.text.includes("换名")), JSON.stringify(btns));
    check("换名钮 data-plat/new-old 齐（番茄）",
      btns.some((b) => b.plat === "fanqie" && b.old === "戍边骑奴"), JSON.stringify(btns));

    // B) 点番茄换名：立即禁用+「换名中…」（防双击），请求 URL/body 正确
    await evalJson(`(() => {
      const b = document.querySelector('#rd-bookmeta .bm-f button[data-plat="fanqie"][onclick="bmRename(this)"]');
      b.click(); return "clicked";
    })()`);
    const during = await evalJson(`(() => {
      const b = document.querySelector('#rd-bookmeta .bm-f button[data-plat="fanqie"][onclick="bmRename(this)"]');
      return { text: b ? b.textContent.trim() : "(gone)", disabled: b ? b.disabled : null };
    })()`);
    check("点击后立即禁用+「换名中…」",
      during.disabled === true && during.text.includes("换名中"), JSON.stringify(during));
    let calls = [];
    for (let i = 0; i < 30; i++) {
      await sleep(200);
      calls = (await evalJson(`window.__rnCalls`)) || [];
      if (calls.length) break;
    }
    check("请求打到 rename 接口且 body 带平台",
      calls.length === 1 && calls[0].url.includes("/api/tasks/" + TASK_ID + "/book-meta/rename")
      && String(calls[0].body).includes("fanqie"), JSON.stringify(calls));

    // C) resolve 后：toast「已换名」含新书名；按钮恢复可点
    // finally 的 refreshState 内层 api 带 15s 超时上限，等待窗口给到 25s
    let after = null;
    for (let i = 0; i < 100; i++) {
      await sleep(250);
      after = await evalJson(`(() => {
        const b = document.querySelector('#rd-bookmeta .bm-f button[data-plat="fanqie"][onclick="bmRename(this)"]');
        const t = document.querySelector("#toast");
        return { text: b ? b.textContent.trim() : "(gone)", disabled: b ? b.disabled : null,
          toast: t ? t.textContent : "", renaming: (window.S || {}).bmRenaming || "" };
      })()`);
      if (after.text.includes("换名") && !after.text.includes("换名中")) break;
    }
    check("toast 报「已换名」且带新名",
      after.toast.includes("已换名") && after.toast.includes("烽燧月")
        && after.toast.includes("戍边骑奴"), after.toast);
    check("按钮恢复可点（换名中态归位）",
      after.disabled === false && after.text.includes("换名") && !after.text.includes("换名中"),
      JSON.stringify(after) + "｜S.bmRenaming=" + after.renaming);

    // D) 失败路径：stub 回 400 撞名 → toast 报错，字段原样
    await evalJson(`(() => {
      const b = document.querySelector('#rd-bookmeta .bm-f button[data-plat="fanqie"][onclick="bmRename(this)"]');
      b.click(); return "clicked2";
    })()`);
    let fail = null;
    for (let i = 0; i < 40; i++) {
      await sleep(250);
      fail = await evalJson(`(() => {
        const b = document.querySelector('#rd-bookmeta .bm-f button[data-plat="fanqie"][onclick="bmRename(this)"]');
        const t = document.querySelector("#toast");
        return { toast: t ? t.textContent : "", disabled: b ? b.disabled : null };
      })()`);
      if (fail.toast.includes("换名失败")) break;
    }
    check("失败 toast 报「换名失败」带死因",
      fail.toast.includes("换名失败") && fail.toast.includes("仍与本站已有作品重名"),
      fail.toast);
    const kept = await evalJson(`(() => {
      const card = document.querySelector('#rd-bookmeta .bm-card.st-done');
      const name = Array.from(card.querySelectorAll(".bm-f"))
        .find((f) => f.querySelector(".bm-f-k").textContent.includes("作品名"));
      return { name: name.querySelector(".bm-f-v").textContent.trim(),
        fields: card.querySelectorAll(".bm-f").length };
    })()`);
    check("失败后书名与字段数原样（不被重摇覆盖）",
      kept.name === "戍边骑奴" && kept.fields === 14, JSON.stringify(kept));

    check("无控制台错误", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));
  } finally {
    try { edge.kill(); } catch (e) { /* noop */ }
    try { srv.kill(); } catch (e) { /* noop */ }
    await sleep(500);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* noop */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* noop */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n通过 %d / 失败 %d", results.length - bad.length, bad.length);
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error("测试崩溃:", e); process.exit(2); });
