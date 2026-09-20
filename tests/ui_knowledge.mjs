/* 知识库页核验：Edge headless + CDP（临时服务端口 18841，CDP 9347）。
 * 1) /api/knowledge 透出 entries/tags/tag_counts/drafts + stale 标记；
 * 2) 设置导航有「知识库」入口，子页卡片/状态 chips（全部/草稿/已转正）/标签 chips/计数齐；
 * 3) 草稿 chip 过滤 + 「转正」流转（API drafts 1→0）；
 * 4) 搜索框标题命中过滤；标签 chip 过滤与还原；
 * 5) 新建弹框保存 → 直接转正入库（total+1、approved）；
 * 6) 编辑不改状态；删除回收（total 还原）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18841;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9347;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};

const NOW = "2026-09-20 10:00:00";
const seedEntry = (id, scope, title, body, tags, status, as_of, hits, seen) => ({
  id, scope, title, body, tags, status, enabled: true, as_of, hits, seen,
  kind: "knowledge", revisions: [], source: "", created_at: NOW, updated_at: NOW,
});
const SEED = {
  entries: [
    seedEntry("kb-a1", "novel", "番茄签约模式", "连载与完本两种模式，建书时选定。", ["番茄", "签约"], "approved", "2026-09-01", 1, 1),
    seedEntry("kb-a2", "novel", "七猫频道级联", "一级分类决定频道，二级须匹配。", ["七猫"], "draft", "2026-08-15", 0, 1),
    seedEntry("kb-a3", "*", "旧平台规则", "早已过期的示例规则。", ["规则"], "approved", "2000-01-01", 2, 3),
  ],
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-kb-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "knowledge.json"), JSON.stringify(SEED), "utf-8");

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

    /* 1) API 透出 */
    const api0 = await (await fetch(SERVICE + "/api/knowledge")).json();
    check("/api/knowledge total/drafts", api0.total === 3 && api0.drafts === 1,
      JSON.stringify({ total: api0.total, drafts: api0.drafts }));
    check("/api/knowledge tags 聚合", (api0.tags || []).includes("番茄") && (api0.tags || []).includes("七猫"),
      JSON.stringify(api0.tags));
    const staleMap = Object.fromEntries((api0.entries || []).map((x) => [x.id, x.stale]));
    check("stale 标记：旧条 true / 新条 false", staleMap["kb-a3"] === true && staleMap["kb-a1"] === false,
      JSON.stringify(staleMap));

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
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    await evalJs(`window.alert=()=>true;window.uiConfirm=async()=>true;`);

    /* 2) 入口 + 子页骨架 */
    const s0 = JSON.parse(await evalJs(`(async () => {
      const nav = !!document.querySelector('.set-item[data-sub="knowledge"]');
      switchTab("knowledge");
      await new Promise(r => setTimeout(r, 1500));
      const chips = [...document.querySelectorAll("#kb-status-chips .cat-chip")].map(c => c.textContent.trim());
      const tagChips = [...document.querySelectorAll("#kb-tag-chips .cat-chip")].map(c => c.textContent.trim());
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      const badges = cards.map(c => (c.querySelector(".tag.warn, .tag.ok") || {}).textContent);
      const stale = cards.filter(c => [...c.querySelectorAll(".tag")].some(tg => tg.textContent === "可能过期")).length;
      return JSON.stringify({
        nav, nCards: cards.length, chips, tagChips,
        count: (document.getElementById("kb-count") || {}).textContent,
        badges, stale,
        hasSearch: !!document.getElementById("kb-search"),
      });
    })()`));
    check("设置导航有「知识库」入口", s0.nav === true, s0.nav);
    check("状态 chips 全部3/草稿1/已转正2", /全部\s*3/.test(s0.chips.join("|")) &&
      /草稿\s*1/.test(s0.chips.join("|")) && /已转正\s*2/.test(s0.chips.join("|")), JSON.stringify(s0.chips));
    check("标签 chips 含全部标签+4 个标签（a1 双标签各自成 chip）", s0.tagChips.length === 5 && s0.tagChips[0].startsWith("全部标签"),
      JSON.stringify(s0.tagChips));
    check("3 张卡片 + 草稿/已转正徽章齐", s0.nCards === 3 && s0.badges.filter(b => b === "草稿").length === 1 &&
      s0.badges.filter(b => b === "已转正").length === 2, JSON.stringify(s0.badges));
    check("过期条目带「可能过期」徽章", s0.stale === 1, String(s0.stale));
    check("搜索框在位 + 计数 3 / 共 3 条", s0.hasSearch && /3\s*\/\s*共\s*3/.test(s0.count || ""), s0.count);

    /* 3) 草稿过滤 + 转正流转 */
    const s1 = JSON.parse(await evalJs(`(async () => {
      [...document.querySelectorAll("#kb-status-chips .cat-chip")].find(c => c.textContent.includes("草稿")).click();
      await new Promise(r => setTimeout(r, 400));
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      const title = cards[0] && cards[0].querySelector(".name").textContent;
      const approveBtn = [...(cards[0]?.querySelectorAll("button") || [])].find(b => b.textContent === "转正");
      approveBtn.click();
      await new Promise(r => setTimeout(r, 1200));
      return JSON.stringify({ title, after: document.querySelectorAll("#kb-cards .card").length });
    })()`));
    check("草稿 chip 只剩「七猫频道级联」且带转正按钮", s1.title === "七猫频道级联", JSON.stringify(s1));
    const api1 = await (await fetch(SERVICE + "/api/knowledge")).json();
    check("转正后 drafts 1→0", api1.drafts === 0, JSON.stringify({ drafts: api1.drafts }));
    check("转正后草稿 chip 下空", s1.after === 0, String(s1.after));
    await evalJs(`(async () => {
      [...document.querySelectorAll("#kb-status-chips .cat-chip")].find(c => c.textContent.includes("全部")).click();
      await new Promise(r => setTimeout(r, 300));
    })()`);

    /* 4) 搜索 + 标签过滤 */
    const s2 = JSON.parse(await evalJs(`(async () => {
      const inp = document.getElementById("kb-search");
      inp.value = "番茄";
      inp.dispatchEvent(new Event("input"));
      await new Promise(r => setTimeout(r, 400));
      const hit = document.querySelectorAll("#kb-cards .card").length;
      inp.value = "";
      inp.dispatchEvent(new Event("input"));
      await new Promise(r => setTimeout(r, 300));
      const restored = document.querySelectorAll("#kb-cards .card").length;
      [...document.querySelectorAll("#kb-tag-chips .cat-chip")].find(c => c.textContent.includes("七猫")).click();
      await new Promise(r => setTimeout(r, 400));
      const tagHit = document.querySelectorAll("#kb-cards .card").length;
      [...document.querySelectorAll("#kb-tag-chips .cat-chip")].find(c => c.textContent.includes("全部标签")).click();
      await new Promise(r => setTimeout(r, 300));
      return JSON.stringify({ hit, restored, tagHit });
    })()`));
    check("搜索「番茄」命中 1 条，清空还原 3 条", s2.hit === 1 && s2.restored === 3, JSON.stringify(s2));
    check("标签 chip「七猫」命中 1 条并可还原", s2.tagHit === 1, JSON.stringify(s2));

    /* 4b) 含撇号的标签不截断内联 onclick（jsq 转义）
     * 注意：写接口需设备控制权，裸 fetch 是「另一台设备」会被 423 拦截，
     * 必须走页面内的 api()（与真实 UI 同一条控制权通道）。 */
    const sEsc = JSON.parse(await evalJs(`(async () => {
      const created = await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({
        op: "create", fields: { title: "撇号标签条目", body: "验证转义。", scope: "code", tags: ["it's", "a'b"] },
      }) }).then(() => true).catch(() => false);
      await loadKnowledge();
      await new Promise(r => setTimeout(r, 600));
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      const c = cards.find(x => x.querySelector(".name").textContent === "撇号标签条目");
      const tags = [...(c ? c.querySelectorAll(".tag") : [])].filter(tg => tg.getAttribute("onclick"));
      const target = tags.find(tg => tg.textContent === "it's");
      if (target) target.click();
      await new Promise(r => setTimeout(r, 500));
      const filtered = [...document.querySelectorAll("#kb-cards .card")];
      const filteredTitles = filtered.map(x => x.querySelector(".name").textContent);
      kbSetFilter("tag", "");
      await new Promise(r => setTimeout(r, 300));
      const id = (KB.data.entries || []).find(x => x.title === "撇号标签条目").id;
      await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({ id, op: "delete" }) });
      await loadKnowledge();
      await new Promise(r => setTimeout(r, 600));
      return JSON.stringify({
        created, tagTexts: tags.map(tg => tg.textContent), filteredTitles,
        afterCleanup: (KB.data.entries || []).length,
      });
    })()`));
    check("含撇号标签条目创建成功", sEsc.created === true, JSON.stringify(sEsc));
    check("撇号标签渲染完整（未被截断）", (sEsc.tagTexts || []).includes("it's") && (sEsc.tagTexts || []).includes("a'b"),
      JSON.stringify(sEsc.tagTexts));
    check("点撇号标签能正确过滤（onclick 未语法错）", (sEsc.filteredTitles || []).length === 1 &&
      sEsc.filteredTitles[0] === "撇号标签条目", JSON.stringify(sEsc.filteredTitles));
    check("撇号用例清理干净（回到 3 条）", sEsc.afterCleanup === 3, String(sEsc.afterCleanup));

    /* 5) 新建弹框 → 保存即转正 */
    const s3 = JSON.parse(await evalJs(`(async () => {
      kbFormOpen(null);
      await new Promise(r => setTimeout(r, 300));
      document.getElementById("kb-f-title").value = "UI 新建条目";
      document.getElementById("kb-f-body").value = "弹框保存的正文。";
      document.getElementById("kb-f-tags").value = "测试, UI";
      document.getElementById("kb-f-scope").value = "code";
      document.getElementById("kb-f-asof").value = "2026-09-20";
      await kbFormSave("");
      await new Promise(r => setTimeout(r, 1200));
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      const nc = cards.find(c => c.querySelector(".name").textContent === "UI 新建条目");
      return JSON.stringify({
        modalGone: document.getElementById("modal").classList.contains("hidden"),
        nCards: cards.length,
        created: !!nc,
        approved: !!nc && !!nc.querySelector(".tag.ok"),
      });
    })()`));
    check("新建弹框保存后关框 + 新卡片在列", s3.modalGone && s3.created, JSON.stringify(s3));
    check("手动新建直接「已转正」", s3.approved === true, JSON.stringify(s3));
    const api2 = await (await fetch(SERVICE + "/api/knowledge")).json();
    const created = (api2.entries || []).find((x) => x.title === "UI 新建条目");
    check("API total 3→4，新条 scope=code/tags 齐全", api2.total === 4 && created &&
      created.scope === "code" && (created.tags || []).join(",") === "测试,UI",
      JSON.stringify({ total: api2.total, e: created && { scope: created.scope, tags: created.tags } }));

    /* 6) 版式：标题独占行不截断 / 徽章与标签分行 / 操作行钉底对齐 / 标签区限高 */
    const sLayout = JSON.parse(await evalJs(`(async () => {
      await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({
        op: "create", fields: {
          title: "番茄平台签约模式分为连载模式与完本模式两种且建书时必须选定其一",
          body: "比较长的正文用于撑高卡片，验证同一行卡片高度一致且按钮底部对齐。",
          scope: "serial_novel",
          tags: ["番茄", "签约", "平台规则", "建书", "连载", "完本", "选题", "上架"],
        } }),
      });
      await loadKnowledge();
      await new Promise(r => setTimeout(r, 700));
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      const target = cards.find(c => c.querySelector(".name").textContent.indexOf("签约模式分为") >= 0);
      const nm = target.querySelector(".name");
      const meta = target.querySelector(".kb-meta");
      const ops = target.querySelector(".ops");
      const cardR = target.getBoundingClientRect(), opsR = ops.getBoundingClientRect();
      const nmR = nm.getBoundingClientRect(), mtR = meta.getBoundingClientRect();
      // 同一行卡片（top 相近）的按钮底边是否对齐
      const sameRow = cards.filter(c => Math.abs(c.getBoundingClientRect().top - cardR.top) < 4);
      const gaps = sameRow.map(c => {
        const r = c.querySelector(".ops").getBoundingClientRect(), cr = c.getBoundingClientRect();
        return Math.round(cr.bottom - r.bottom);
      });
      const chipBox = document.getElementById("kb-tag-chips");
      const cs = getComputedStyle(chipBox);
      const res = {
        nameText: nm.textContent,
        nameClamp: getComputedStyle(nm).webkitLineClamp,
        // 标题未被省略号截断：渲染宽度撑满卡片内容区（独占行）
        nameFillsRow: Math.abs(nmR.width - (cardR.width - 32)) < 24,
        metaBelowName: mtR.top >= nmR.bottom - 1,
        metaHoldsBadgesAndTags: !!meta.querySelector(".tag.warn, .tag.ok") &&
          !!meta.querySelector(".tag.kt"),
        metaWrapsUnderTitle: mtR.height <= 60 && mtR.width > nmR.width * 0.9,
        opsPinned: Math.round(cardR.bottom - opsR.bottom) <= 18,
        opsGapsEqual: new Set(gaps).size === 1,
        opsGaps: gaps,
        chipMaxH: cs.maxHeight,
        chipOverflow: cs.overflowY,
      };
      // 清理本用例新增条目
      const id = (KB.data.entries || []).find(x => x.title.indexOf("签约模式分为") >= 0).id;
      await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({ id, op: "delete" }) });
      await loadKnowledge();
      await new Promise(r => setTimeout(r, 600));
      res.afterCleanup = (KB.data.entries || []).length;
      return JSON.stringify(res);
    })()`));
    check("长标题完整渲染（未被省略号截断）", sLayout.nameText.indexOf("签约模式分为") >= 0 &&
      sLayout.nameFillsRow === true, JSON.stringify(sLayout));
    check("标题两行截断策略生效（-webkit-line-clamp:2）", sLayout.nameClamp === "2", sLayout.nameClamp);
    check("状态徽章与可点标签合并到标题下方元信息行（换行自适应）", sLayout.metaBelowName === true &&
      sLayout.metaHoldsBadgesAndTags === true && sLayout.metaWrapsUnderTitle === true, JSON.stringify(sLayout));
    check("操作行钉底且同行卡片底边对齐", sLayout.opsPinned === true && sLayout.opsGapsEqual === true,
      JSON.stringify(sLayout.opsGaps));
    check("标签 chips 区限高可滚动", sLayout.chipMaxH === "82px" && sLayout.chipOverflow === "auto",
      sLayout.chipMaxH + "/" + sLayout.chipOverflow);
    check("版式用例清理干净（回到 4：3 条种子 + 上一步新建）", sLayout.afterCleanup === 4, String(sLayout.afterCleanup));


    /* 7) 编辑不改状态 + 删除回收 */
    const s4 = JSON.parse(await evalJs(`(async () => {
      kbEditOpen("kb-a1");
      await new Promise(r => setTimeout(r, 300));
      const titleFilled = document.getElementById("kb-f-title").value === "番茄签约模式";
      document.getElementById("kb-f-body").value = "编辑后的正文。";
      await kbFormSave("kb-a1");
      await new Promise(r => setTimeout(r, 1200));
      return JSON.stringify({ titleFilled });
    })()`));
    check("编辑弹框预填标题", s4.titleFilled === true, JSON.stringify(s4));
    const api3 = await (await fetch(SERVICE + "/api/knowledge")).json();
    const a1 = (api3.entries || []).find((x) => x.id === "kb-a1");
    check("编辑后正文更新且状态仍 approved", a1 && a1.body === "编辑后的正文。" && a1.status === "approved",
      JSON.stringify(a1 && { body: a1.body, status: a1.status }));
    await evalJs(`kbOp(${JSON.stringify(created.id)}, "delete")`);
    await sleep(900);
    const api4 = await (await fetch(SERVICE + "/api/knowledge")).json();
    check("删除后 total 回到 3", api4.total === 3, String(api4.total));

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
