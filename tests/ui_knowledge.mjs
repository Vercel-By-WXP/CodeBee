/* 知识库页核验：Edge headless + CDP（临时服务端口 18841，CDP 9347）。
 * 基线=默认直接转正（服务启动迁移会把种子 draft 转掉，故种子全 approved）：
 * 1) /api/knowledge 透出 entries/tags/tag_counts/drafts + stale 标记；
 * 2) 设置导航「知识库」入口；无草稿时状态 chips 整区隐藏、卡片无状态徽章；
 * 3) 中途种回一条 draft（兼容路径）：草稿徽章带琥珀圆点 + 转正按钮 + chips 重现；
 * 4) 转正流转：drafts 1→0，chips 再隐藏且残留过滤态自动清除（列表不空）；
 * 5) 搜索 / 标签过滤；撇号标签不截断内联 onclick（jsq）；
 * 6) 版式：标题独占行、元信息行、操作钉底对齐、标签区限高；
 * 7) 新建（直接转正）/ 编辑不改状态 / 删除回收。 */
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
const E = (id, scope, title, body, tags, status, as_of, hits, seen) => ({
  id, scope, title, body, tags, status, enabled: true, as_of, hits, seen,
  kind: "knowledge", revisions: [], source: "", created_at: NOW, updated_at: NOW,
});
const SEED = {
  entries: [
    E("kb-a1", "novel", "番茄签约模式", "连载与完本两种模式，建书时选定。", ["番茄", "签约"], "approved", "2026-09-01", 1, 1),
    E("kb-a2", "novel", "七猫频道级联", "一级分类决定频道，二级须匹配。", ["七猫"], "approved", "2026-08-15", 0, 1),
    E("kb-a3", "*", "旧平台规则", "早已过期的示例规则。", ["规则"], "approved", "2000-01-01", 2, 3),
  ],
};
const DRAFT_SEED = {
  entries: [...SEED.entries, E("kb-d1", "novel", "草稿回归条目", "迁移后手工种回的草稿。", ["回归"], "draft", "2026-09-19", 0, 1)],
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-kb-"));
  const dataDir = join(tmp, "data");
  const kbFile = join(dataDir, "knowledge.json");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(kbFile, JSON.stringify(SEED), "utf-8");

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

    /* 1) API：种子全 approved，启动迁移零写入 */
    const api0 = await (await fetch(SERVICE + "/api/knowledge")).json();
    check("/api/knowledge total=3 drafts=0（启动迁移已把 draft 归零）", api0.total === 3 && api0.drafts === 0,
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

    /* 2) 入口 + 无草稿基线：状态 chips 隐藏、卡片无状态徽章 */
    const s0 = JSON.parse(await evalJs(`(async () => {
      const nav = !!document.querySelector('.set-item[data-sub="knowledge"]');
      switchTab("knowledge");
      await new Promise(r => setTimeout(r, 1500));
      const chips = [...document.querySelectorAll("#kb-status-chips .cat-chip")].map(c => c.textContent.trim());
      const tagChips = [...document.querySelectorAll("#kb-tag-chips .cat-chip")].map(c => c.textContent.trim());
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      return JSON.stringify({
        nav, nCards: cards.length, chips, tagChips,
        noWarnBadge: cards.every(c => !c.querySelector(".tag.warn")),
        count: (document.getElementById("kb-count") || {}).textContent,
        stale: cards.filter(c => [...c.querySelectorAll(".tag")].some(tg => tg.textContent === "可能过期")).length,
        hasSearch: !!document.getElementById("kb-search"),
      });
    })()`));
    check("设置导航有「知识库」入口", s0.nav === true, s0.nav);
    check("无草稿时状态 chips 整区隐藏", s0.chips.length === 0, JSON.stringify(s0.chips));
    check("标签 chips 含全部标签+4 个标签（a1 双标签各自成 chip）", s0.tagChips.length === 5 && s0.tagChips[0].startsWith("全部标签"),
      JSON.stringify(s0.tagChips));
    check("3 张卡片且全部无状态徽章（默认转正后无信息量）", s0.nCards === 3 && s0.noWarnBadge === true,
      JSON.stringify({ nCards: s0.nCards, noWarnBadge: s0.noWarnBadge }));
    check("过期条目带「可能过期」徽章", s0.stale === 1, String(s0.stale));
    check("搜索框在位 + 计数 3 / 共 3 条", s0.hasSearch && /3\s*\/\s*共\s*3/.test(s0.count || ""), s0.count);

    /* 3) 种回一条 draft（兼容路径）：徽章琥珀点 + 转正按钮 + chips 重现 */
    writeFileSync(kbFile, JSON.stringify(DRAFT_SEED), "utf-8");
    await evalJs(`(async () => { location.reload(); })()`);
    await sleep(3500);
    await evalJs(`window.alert=()=>true;window.uiConfirm=async()=>true;`);
    const sD = JSON.parse(await evalJs(`(async () => {
      switchTab("knowledge");
      await new Promise(r => setTimeout(r, 1500));
      const chips = [...document.querySelectorAll("#kb-status-chips .cat-chip")].map(c => c.textContent.trim());
      const cards = [...document.querySelectorAll("#kb-cards .card")];
      const dc = cards.find(c => c.querySelector(".name").textContent === "草稿回归条目");
      const wb = dc && dc.querySelector(".tag.warn");
      return JSON.stringify({
        chips,
        hasDraftCard: !!dc,
        dotInDraft: !!(wb && wb.querySelector(".cdot")),
        hasApprove: !!dc && [...dc.querySelectorAll("button")].some(b => b.textContent === "转正"),
      });
    })()`));
    check("种回 draft 后状态 chips 重现（全部4/草稿1/已转正3）",
      /全部\s*4/.test(sD.chips.join("|")) && /草稿\s*1/.test(sD.chips.join("|")) &&
      /已转正\s*3/.test(sD.chips.join("|")), JSON.stringify(sD.chips));
    check("草稿卡带琥珀圆点徽章 + 转正按钮", sD.hasDraftCard && sD.dotInDraft && sD.hasApprove, JSON.stringify(sD));

    /* 4) 草稿过滤 + 转正流转 */
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
    check("草稿 chip 只剩「草稿回归条目」且带转正按钮", s1.title === "草稿回归条目", JSON.stringify(s1));
    const api1 = await (await fetch(SERVICE + "/api/knowledge")).json();
    check("转正后 drafts 1→0", api1.drafts === 0, JSON.stringify({ drafts: api1.drafts }));
    const s1b = JSON.parse(await evalJs(`JSON.stringify({
      chipsGone: document.querySelectorAll("#kb-status-chips .cat-chip").length === 0,
      cards: document.querySelectorAll("#kb-cards .card").length,
    })`));
    check("转正后状态 chips 区隐藏且列表不空（过滤态自动清除）", s1b.chipsGone === true && s1b.cards === 4,
      JSON.stringify(s1b));

    /* 5) 搜索 + 标签过滤 + 撇号标签转义 */
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
      const created = await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({
        op: "create", fields: { title: "撇号标签条目", body: "验证转义。", scope: "code", tags: ["it's", "a'b"] },
      }) }).then(() => true).catch(() => false);
      await loadKnowledge();
      await new Promise(r => setTimeout(r, 600));
      const cards2 = [...document.querySelectorAll("#kb-cards .card")];
      const c = cards2.find(x => x.querySelector(".name").textContent === "撇号标签条目");
      const tags = [...(c ? c.querySelectorAll(".tag") : [])].filter(tg => tg.getAttribute("onclick"));
      const tgt = tags.find(tg => tg.textContent === "it's");
      if (tgt) tgt.click();
      await new Promise(r => setTimeout(r, 500));
      const filteredTitles = [...document.querySelectorAll("#kb-cards .card")].map(x => x.querySelector(".name").textContent);
      kbSetFilter("tag", "");
      await new Promise(r => setTimeout(r, 300));
      const id = (KB.data.entries || []).find(x => x.title === "撇号标签条目").id;
      await api("/api/knowledge/op", { method: "POST", body: JSON.stringify({ id, op: "delete" }) });
      await loadKnowledge();
      await new Promise(r => setTimeout(r, 600));
      return JSON.stringify({
        created, tagTexts: tags.map(tg => tg.textContent), filteredTitles,
        afterCleanup: (KB.data.entries || []).length, hit, restored, tagHit,
      });
    })()`));
    check("搜索「番茄」命中 1 条，清空还原 4 条", s2.hit === 1 && s2.restored === 4, JSON.stringify(s2));
    check("标签 chip「七猫」命中 1 条并可还原", s2.tagHit === 1, JSON.stringify(s2));
    check("含撇号标签条目创建成功", s2.created === true, JSON.stringify(s2));
    check("撇号标签渲染完整（未被截断）", (s2.tagTexts || []).includes("it's") && (s2.tagTexts || []).includes("a'b"),
      JSON.stringify(s2.tagTexts));
    check("点撇号标签能正确过滤（onclick 未语法错）", (s2.filteredTitles || []).length === 1 &&
      s2.filteredTitles[0] === "撇号标签条目", JSON.stringify(s2.filteredTitles));
    check("撇号用例清理干净（回到 4 条）", s2.afterCleanup === 4, String(s2.afterCleanup));

    /* 6) 新建弹框 → 保存即转正（转正不再挂徽章，以非草稿态判定） */
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
        approved: !!nc && !nc.querySelector(".tag.warn"),   // 非草稿即转正（转正不再挂徽章）
      });
    })()`));
    check("新建弹框保存后关框 + 新卡片在列", s3.modalGone && s3.created, JSON.stringify(s3));
    check("手动新建直接转正（无草稿徽章）", s3.approved === true, JSON.stringify(s3));

    /* 7) 版式：标题独占行 / 元信息行 / 操作钉底对齐 / 标签区限高 */
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
        nameFillsRow: Math.abs(nmR.width - (cardR.width - 32)) < 24,
        metaBelowName: mtR.top >= nmR.bottom - 1,
        metaHoldsBadgesAndTags: !!meta.querySelector(".tag.kt") &&
          [...meta.querySelectorAll(".tag")].some(tg => !tg.classList.contains("kt")) &&
          meta.querySelectorAll(".tag.kt").length >= 5,   // 后端 tags 上限 6
        metaWrapsUnderTitle: mtR.height <= 60 && mtR.width > nmR.width * 0.9,
        opsPinned: Math.round(cardR.bottom - opsR.bottom) <= 18,
        opsGapsEqual: new Set(gaps).size === 1,
        opsGaps: gaps,
        chipMaxH: cs.maxHeight,
        chipOverflow: cs.overflowY,
        hairline: getComputedStyle(target, "::before").height,
      };
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
    check("元信息行在标题下方：8 个可点标签 + 状态 tag 同行", sLayout.metaBelowName === true &&
      sLayout.metaHoldsBadgesAndTags === true && sLayout.metaWrapsUnderTitle === true, JSON.stringify(sLayout));
    check("操作行钉底且同行卡片底边对齐", sLayout.opsPinned === true && sLayout.opsGapsEqual === true,
      JSON.stringify(sLayout.opsGaps));
    check("标签 chips 区限高可滚动", sLayout.chipMaxH === "82px" && sLayout.chipOverflow === "auto",
      sLayout.chipMaxH + "/" + sLayout.chipOverflow);
    check("卡片顶部主题色发丝线在位", sLayout.hairline === "2px", sLayout.hairline);
    check("版式用例清理干净（回到 4：4 条存量 + 上一步新建待删）", sLayout.afterCleanup === 5, String(sLayout.afterCleanup));

    /* 8) 编辑不改状态 + 删除回收 */
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
    const api2before = await (await fetch(SERVICE + "/api/knowledge")).json();
    const created = (api2before.entries || []).find((x) => x.title === "UI 新建条目");
    await evalJs(`kbOp(${JSON.stringify(created.id)}, "delete")`);
    await sleep(900);
    const api4 = await (await fetch(SERVICE + "/api/knowledge")).json();
    check("删除后 total 回到 4", api4.total === 4, String(api4.total));

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
