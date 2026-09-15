/* 经验库「自动教训分类过滤」核验（chip 版）：Edge headless + CDP（临时服务端口 18814）。
 * 1) 分类 chip 条存在：含「全部」+ 各分类药丸（圆点配色 + 计数），不再用下拉框；
 * 2) 默认全部高亮，卡片带彩色分类 tag；
 * 3) 点某分类 chip → active 迁移 + 只剩该类卡片 + 计数「N / 共 M 条」；
 * 4) 点 0 条的枚举分类 → 空态「该分类下暂无教训」，chip 仍可点回全部；
 * 5) 老数据（无 category）归未分类：有「未分类」chip 且能命中；
 * 6) 点卡片上的分类 tag 也能过滤（tag 即快捷入口）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18814;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9344;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-skillcat-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  // 预置 data/skills.json：3 条带分类 + 1 条老数据（无 category）
  writeFileSync(join(dataDir, "skills.json"), JSON.stringify({
    lessons: [
      { id: "sk-t1", scope: "serial_novel", title: "章末无钩子", content: "每章结尾断章要狠", hits: 0, seen: 2, enabled: true, category: "节奏爽点", kind: "lesson" },
      { id: "sk-t2", scope: "serial_novel", title: "人设崩塌", content: "主角动机前置", hits: 0, seen: 1, enabled: true, category: "人物塑造", kind: "lesson" },
      { id: "sk-t3", scope: "serial_novel", title: "设定矛盾", content: "建立设定台账", hits: 0, seen: 1, enabled: true, category: "一致性", kind: "lesson" },
      { id: "sk-t4", scope: "serial_novel", title: "老数据条目", content: "无分类字段", hits: 0, seen: 1, enabled: true, kind: "lesson" },
    ],
    packs: {},
  }), "utf-8");

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
    check("临时服务启动", up);
    // 后端 view 接口先确认分类/计数已透出
    const api = await (await fetch(SERVICE + "/api/skills")).json();
    check("/api/skills 透出 categories", Array.isArray(api.categories) && api.categories.includes("一致性"), JSON.stringify(api.categories));
    check("/api/skills 透出 counts + total", api.total === 4 && api.counts["节奏爽点"] === 1 && api.counts["未分类"] === 1, JSON.stringify(api.counts));

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
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Runtime.evaluate", { expression: `window.alert=()=>true;window.confirm=()=>true;` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 1-2) 进经验库页：chip 条存在 + 默认全部 */
    const st = await evalJs(`(async () => {
      switchTab("skills");
      await new Promise(r => setTimeout(r, 1500));
      const chips = [...document.querySelectorAll("#skill-cat-chips .cat-chip")];
      const cards = document.querySelectorAll("#skill-lessons .card");
      const cats = [...document.querySelectorAll("#skill-lessons .tag.cat")];
      const hasSelect = !!document.getElementById("skill-cat-filter");
      return JSON.stringify({
        nChips: chips.length,
        texts: chips.map(c => c.textContent.trim()),
        active: chips.find(c => c.classList.contains("active"))?.textContent.trim(),
        dots: chips.filter(c => c.querySelector(".cdot")).length,
        nCards: cards.length,
        tags: cats.map(e => e.textContent),
        tagColor: cats[0] && cats[0].style.color,
        hasSelect,
        count: (document.getElementById("skill-count") || {}).textContent,
      });
    })()`);
    const s0 = JSON.parse(st);
    check("下拉框已移除", !s0.hasSelect, st);
    check("chip 条含「全部」+ 7 类（6 枚举+未分类）", s0.nChips === 8 && s0.texts[0].startsWith("全部"), st);
    check("chip 带计数（全部 4 / 节奏爽点 1 / 未分类 1）", /全部\s*4/.test(s0.texts.join("|")) &&
      s0.texts.some(x => /节奏爽点\s*1/.test(x)) && s0.texts.some(x => /未分类\s*1/.test(x)), st);
    check("默认「全部」高亮且展示 4 条", /全部/.test(s0.active || "") && s0.nCards === 4, st);
    check("除全部外各 chip 带圆点", s0.dots === 7, st);
    check("卡片带彩色分类 tag（3 条有类，老数据无）", s0.tags.length === 3 && !!s0.tagColor, st);

    /* 3) 点「一致性」chip */
    const pick = await evalJs(`(async () => {
      const chip = [...document.querySelectorAll("#skill-cat-chips .cat-chip")]
        .find(c => c.textContent.includes("一致性"));
      chip.click();
      await new Promise(r => setTimeout(r, 400));
      const cards = [...document.querySelectorAll("#skill-lessons .card")];
      const act = [...document.querySelectorAll("#skill-cat-chips .cat-chip.active")];
      return JSON.stringify({
        nCards: cards.length,
        title: cards[0] && cards[0].querySelector(".name").textContent,
        count: (document.getElementById("skill-count") || {}).textContent,
        active: act.length === 1 && act[0].textContent.includes("一致性"),
      });
    })()`);
    const s1 = JSON.parse(pick);
    check("点「一致性」只剩 1 条（设定矛盾）", s1.nCards === 1 && s1.title === "设定矛盾", pick);
    check("计数显示 1 / 共 4 条", /1\s*\/\s*共\s*4/.test(s1.count || ""), pick);
    check("选中态互斥迁移到一致性 chip", s1.active === true, pick);

    /* 4) 点 0 条的枚举分类 → 空态不报错，可点回全部 */
    const empty = await evalJs(`(async () => {
      [...document.querySelectorAll("#skill-cat-chips .cat-chip")].find(c => c.textContent.includes("情节逻辑")).click();
      await new Promise(r => setTimeout(r, 400));
      const box = document.getElementById("skill-lessons");
      const emptyShown = box.querySelectorAll(".card").length === 0 && !!box.querySelector(".empty");
      [...document.querySelectorAll("#skill-cat-chips .cat-chip")].find(c => c.textContent.includes("全部")).click();
      await new Promise(r => setTimeout(r, 400));
      return JSON.stringify({ emptyShown, backTo: box.querySelectorAll(".card").length });
    })()`);
    const s2 = JSON.parse(empty);
    check("0 条分类显示空态", s2.emptyShown, empty);
    check("点回「全部」恢复 4 条", s2.backTo === 4, empty);

    /* 5) 未分类 chip 命中老数据 */
    const legacy = await evalJs(`(async () => {
      [...document.querySelectorAll("#skill-cat-chips .cat-chip")].find(c => c.textContent.includes("未分类")).click();
      await new Promise(r => setTimeout(r, 400));
      const cards = [...document.querySelectorAll("#skill-lessons .card")];
      return JSON.stringify({ nCards: cards.length, title: cards[0] && cards[0].querySelector(".name").textContent });
    })()`);
    const s3 = JSON.parse(legacy);
    check("未分类 chip 命中老数据（无 category 字段）", s3.nCards === 1 && s3.title === "老数据条目", legacy);

    /* 6) 点卡片分类 tag 快捷过滤 */
    const tagClick = await evalJs(`(async () => {
      [...document.querySelectorAll("#skill-cat-chips .cat-chip")].find(c => c.textContent.includes("全部")).click();
      await new Promise(r => setTimeout(r, 300));
      const tag = [...document.querySelectorAll("#skill-lessons .tag.cat")]
        .find(e => e.textContent === "人物塑造");
      tag.click();
      await new Promise(r => setTimeout(r, 400));
      const cards = [...document.querySelectorAll("#skill-lessons .card")];
      return JSON.stringify({ nCards: cards.length, title: cards[0] && cards[0].querySelector(".name").textContent });
    })()`);
    const s4 = JSON.parse(tagClick);
    check("点卡片 tag「人物塑造」直接过滤到对应条", s4.nCards === 1 && s4.title === "人设崩塌", tagClick);

    const fails = results.filter((r) => !r.ok);
    console.log(fails.length ? "\n✗ " + fails.length + " 项未过" : "\n全部通过");
    process.exitCode = fails.length ? 1 : 0;
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(2); });
