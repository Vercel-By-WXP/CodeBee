/* 禅道·产品档案 UI 验证：自起临时服务（TUTTI_DATA 隔离 + 种子 zentao.json v2）+ Edge headless。
 * 覆盖：设置导航「禅道」入口、档案卡渲染（产品/负责人/模块路由）、修复记录的
 * 排查徽章与任务链接、保存配置落库（含档案结构与脱敏）、增删产品/路由、
 * 立即扫描对不可达地址优雅报错。
 * 结束清理浏览器/服务进程、临时目录。 */
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18951;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9377);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uizentao-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  // 种子：产品档案 + 一条带排查结论的认领（v2 形状）
  writeFileSync(join(dataDir, "zentao.json"), JSON.stringify({
    version: 2,
    config: { base_url: "http://127.0.0.1:18949", account: "coder", password: "secret",
      product_profiles: [{ product: 7, assigned_to: "coder", severity_cap: 0,
        our_sides: ["backend"],
        repos: { backend: { workdir: "", git_rev: "main", verify_command: "" },
                 frontend: { workdir: "", git_rev: "", verify_command: "" } },
        repo_hints: { backend: "Spring Boot 服务", frontend: "" },
        owners: { backend: "be1", frontend: "fe1", not_ours: "" },
        module_routes: [{ module: 99, side: "backend", account: "" }] }],
      auto_resolve: true, auto_merge: true, triage_ai: true,
      poll_enabled: false, interval_hours: 2 },   // 老键（小时）：加载时迁成分钟
    claims: { "501": { bug_id: 501, product: 7, title: "种子 Bug：登录 500",
        triage: { side: "backend", reason: "模块 #99 路由规则", by: "rule", account: "" },
        tasks: [{ side: "backend", task_id: "t-seed-1", run_id: "r-seed-1", state: "fixing" }],
        state: "fixing", note: "", attempts: 0, claimed_at: "2026-09-19 08:00:00" } },
    last_scan: "2026-09-19 08:00:00", next_scan: "", last_error: "",
  }, null, 1), "utf-8");

  // 并行测试双绑防线：Windows SO_REUSEADDR 允许两个进程同时 listen 同一端口——
  // 若并行代理也在跑本套件，请求会在两个服务间漂移，②-④b 全绿但 ⑤ 时序错乱假失败。
  // 起服务前先探一次：已有监听 = 错开再跑。
  try { await fetch(SERVICE + "/api/state");
    console.error("端口 " + PORT + " 已被占用（可能有并行测试在跑同款临时服务），错开再跑");
    process.exit(2);
  } catch (e) { /* 空闲，继续 */ }

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    cwd: ROOT, stdio: "ignore",
    env: Object.assign({}, process.env, { TUTTI_DATA: dataDir, PYTHONPATH: ROOT }),
  });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE, [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    if (!target) throw new Error("no CDP target");

    ws = new WebSocket(target.webSocketDebuggerUrl);
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
    const evalJs = async (expr, awaitPromise = false) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise });
      return r.result?.result?.value;
    };
    const waitFor = async (expr, ms = 20000) => {
      const wrapped = typeof expr === "function" ? "(" + expr.toString() + ")()" : expr;
      for (let t = 0; t < ms; t += 400) {
        if (await evalJs(wrapped, true)) return true;
        await sleep(400);
      }
      return false;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    await waitFor(`typeof S === "object" && S !== null && S.state !== null`);
    await evalJs(`if (typeof welcomeClose === "function" &&
      !document.getElementById("welcome").classList.contains("hidden")) welcomeClose(); "ok"`);
    await sleep(300);

    // ① 设置导航入口 + 进子页
    check("① 设置导航有「禅道」入口",
      (await evalJs(`!!document.querySelector(".set-item[data-sub='zentao']")`)) === true);
    await evalJs(`switchTab("zentao"); "ok"`);
    const pageOn = await waitFor(
      `!document.getElementById("sub-zentao").classList.contains("hidden")`, 8000);
    check("① 点击进入禅道子页", pageOn === true);

    // ② 产品档案卡 + 修复记录渲染
    await waitFor(`typeof S.zentao === "object" && S.zentao !== null && (S.zentao.config||{}).product_profiles`);
    const prof = await evalJs(`(() => {
      const card = document.querySelector("#zt-profiles .zt-prof");
      if (!card) return null;
      return JSON.stringify({
        count: document.querySelectorAll("#zt-profiles .zt-prof").length,
        product: card.querySelector(".zt-p-product").value,
        rev: card.querySelector('.zt-p-rev[data-side="backend"]').value,
        fe: card.querySelector('.zt-p-owner[data-side="frontend"]').value,
        routes: card.querySelectorAll(".zt-mr-row").length,
        routeModule: (card.querySelector(".zt-mr-module") || {}).value,
        hint: card.querySelector('.zt-p-hint[data-side="backend"]').value
      });
    })()`, true);
    const pf = JSON.parse(prof || "{}");
    check("② 档案卡渲染（产品/分支/负责人/路由/提示）",
      pf.count === 1 && pf.product === "7" && pf.rev === "main" && pf.fe === "fe1" &&
      pf.routes === 1 && pf.routeModule === "99" && pf.hint === "Spring Boot 服务", prof);
    const pwPlaceholder = await evalJs(`document.getElementById("zt-password").placeholder`);
    check("② 已存密码不回显", String(pwPlaceholder || "").indexOf("已保存") >= 0, pwPlaceholder);
    // 种子 claim 是 fixing + 孤儿 run_id：start() 会在启动 3 秒后跑 boot 对账
    // （#27697 死窗案补的），把查无此 run 的 claim 判成 lost——断言按该确定性行为走。
    const claim = await waitFor(`(() => {
      const t = document.getElementById("zentao-claims").textContent;
      return t.indexOf("种子 Bug") >= 0 && t.indexOf("记录丢失") >= 0 && t.indexOf("排查：后端问题") >= 0;
    })()`, 8000);
    check("② 修复记录含排查徽章与状态（孤儿 run 被 boot 对账判「记录丢失」）", claim === true);

    // ②c bug 号一键直达禅道详情：无探测缓存时回落 base_url 拼 GET 形态链接（新老版通用）
    const bugLink = await evalJs(`(() => {
      const a = document.querySelector("#zentao-claims .zt-bug-link");
      if (!a) return "";
      return JSON.stringify({ href: a.getAttribute("href"), target: a.target,
        title: a.title, text: a.textContent.slice(0, 40) });
    })()`, true);
    const bl = JSON.parse(bugLink || "{}");
    check("②c bug 号是链接：新页签打开禅道 bug 详情（GET 形态回落 base_url）",
      bl.href === "http://127.0.0.1:18949/index.php?m=bug&f=view&bugID=501" &&
      bl.target === "_blank" && bl.title.indexOf("禅道") >= 0 &&
      bl.text.indexOf("#501") >= 0 && bl.text.indexOf("种子 Bug") >= 0, bugLink);

    // ②b 仓库·工作目录行：「选择…」按钮 + 占位文案讲真话 + 点选回填同一行（pick_folder 已 stub 防真弹窗）
    await evalJs(`(() => {
      const orig = window.fetch.bind(window);
      window.fetch = (url, opts) => {
        if (String(url).includes("/api/pick_folder")) {
          return Promise.resolve(new Response(JSON.stringify(
            { path: "E:\\\\mockrepo" }),
            { status: 200, headers: { "Content-Type": "application/json" } }));
        }
        return orig(url, opts);
      };
      return 1;
    })()`);
    const wdRow = await evalJs(`(() => {
      const card = document.querySelector("#zt-profiles .zt-prof");
      const rows = card.querySelectorAll(".zt-wd-row");
      const be = rows[0] || null;
      return JSON.stringify({
        count: rows.length,
        hasBtn: be ? !!be.querySelector("button") : false,
        ph: be && be.querySelector(".zt-p-wd") ? be.querySelector(".zt-p-wd").placeholder : "",
        val: be && be.querySelector(".zt-p-wd") ? be.querySelector(".zt-p-wd").value : ""
      });
    })()`, true);
    const wr = JSON.parse(wdRow || "{}");
    check("②b 工作目录行渲染（前后端各两行：目录+基线分支·带选择按钮）",
      wr.count === 4 && wr.hasBtn === true, wdRow);
    check("②b 占位文案改为「空 = 用默认保存路径」",
      wr.ph === "空 = 用默认保存路径", wdRow);
    // 几何回归：档案卡里所有值输入（含工作目录行）不得溢出卡片右缘（窄卡裁切案）
    const geo = await evalJs(`(() => {
      const card = document.querySelector("#zt-profiles .zt-prof");
      const cb = card.getBoundingClientRect();
      let worst = -999, cnt = 0, worstSel = "";
      for (const el of card.querySelectorAll(".zt-grid input, .zt-grid select, .zt-wd-row")) {
        const b = el.getBoundingClientRect();
        if (b.width === 0) continue;
        cnt++;
        if (b.right - cb.right > worst) { worst = b.right - cb.right; worstSel = el.className; }
      }
      return JSON.stringify({ cnt, worst: Math.round(worst), worstSel, cardW: Math.round(cb.width) });
    })()`, true);
    const g = JSON.parse(geo || "{}");
    check("②b 档案卡值输入不溢出卡片右缘", g.cnt >= 10 && g.worst <= 2, geo);
    // 几何回归：禅道连接卡输入框必须紧跟 150px 标签列（auto 标签列会在宽卡吞自由空间，
    // 把输入框推到页面中部——已踩）。偏移 = 卡左缘到输入框左缘，150 列 + gap + 内边距应 ≤240
    const geo2 = await evalJs(`(() => {
      const inp = document.getElementById("zt-base-url");
      const cb = document.getElementById("sub-zentao").getBoundingClientRect();
      return JSON.stringify({ off: Math.round(inp.getBoundingClientRect().left - cb.left) });
    })()`, true);
    const g2 = JSON.parse(geo2 || "{}");
    check("②b 连接卡输入框紧跟标签列（不被推到中部）", g2.off > 100 && g2.off <= 240, geo2);
    await evalJs(`ztWdPick(document.querySelector("#zt-profiles .zt-prof .zt-wd-row button")); "ok"`);
    await sleep(500);
    const wdFilled = await evalJs(
      `document.querySelector("#zt-profiles .zt-prof .zt-wd-row .zt-p-wd").value`);
    check("②b 点「选择…」→ 回填同一行输入框（元素目标链路）", wdFilled === "E:\\mockrepo", wdFilled);
    await evalJs(`const w = document.querySelector("#zt-profiles .zt-prof .zt-wd-row .zt-p-wd");
      w.value = ""; w.dispatchEvent(new Event("input", { bubbles: true })); "ok"`);

    // ③ 档案卡编辑：加路由（填模块 88→前端）+ 改全局开关 → 保存落库
    await evalJs(`ztMrAdd(0);
      const rows = document.querySelectorAll("#zt-profiles .zt-prof")[0].querySelectorAll(".zt-mr-row");
      const last = rows[rows.length - 1];
      last.querySelector(".zt-mr-module").value = "88";
      last.querySelector(".zt-mr-side").value = "frontend";
      last.querySelector(".zt-mr-account").value = "fe2"; saveZentao(); "ok"`);
    const saved = await waitFor(`api("/api/zentao").then(v => {
      const p = (v.config.product_profiles || [])[0] || {};
      return p.product === 7 && (p.module_routes || []).length === 2 &&
        JSON.stringify(p.owners) === JSON.stringify({ backend: "be1", frontend: "fe1", not_ours: "" }) &&
        v.config.has_password === true && v.config.password === "" &&
        v.config.triage_ai === true;
    })`, 10000);
    check("③ 保存后档案结构落库（路由×2 + 负责人 + 脱敏）", saved === true);

    // ④ 加第二个产品自动选首个未配置项 → 保存；全部已添加时不再生成 product=0 空卡
    await evalJs(`S.ztProducts = [{id:7,name:"产品柒",status:"normal"},
      {id:8,name:"产品捌",status:"closed"}]; renderZentaoProfiles(); "ok"`);
    await evalJs(`(async () => { await ztProfAdd(); await saveZentao(); return "ok"; })()`, true);
    const two = await waitFor(`api("/api/zentao").then(v =>
      (v.config.product_profiles || []).length === 2 &&
      v.config.product_profiles[1].product === 8)`, 10000);
    check("④ 添加产品自动选中首个未配置产品（档案×2）", two === true);
    const noEmpty = await evalJs(`(async () => {
      await ztProfAdd();
      return JSON.stringify({ count: document.querySelectorAll("#zt-profiles .zt-prof").length,
        values: Array.from(document.querySelectorAll("#zt-profiles .zt-p-product")).map((x) => x.value) });
    })()`, true);
    const ne = JSON.parse(noEmpty || "{}");
    check("④ 产品均已添加时不创建 product=0 空卡",
      ne.count === 2 && ne.values.every((x) => Number(x) > 0), noEmpty);
    await evalJs(`ztProfDel(1); saveZentao(); "ok"`);
    const one = await waitFor(`api("/api/zentao").then(v =>
      (v.config.product_profiles || []).length === 1)`, 10000);
    check("④ 删除产品并保存（回到×1）", one === true);

    // ④b 清单就位 → 产品字段变下拉（保留已选 7）、负责人挂账号 datalist
    const selInfo = await evalJs(`(() => {
      S.ztProducts = [{id:7,name:"产品柒",status:"normal"},{id:8,name:"产品捌",status:"closed"}];
      S.ztUsers = [{account:"coder",realname:"码蜂"},{account:"fe1",realname:"前端壹"}];
      const dl = document.getElementById("zt-users");
      dl.innerHTML = S.ztUsers.map((u) =>
        '<option value="' + u.account + '">' + (u.realname || "") + '</option>').join("");
      S.ztProfiles = ztHarvestProfiles();
      renderZentaoProfiles();
      const sel = document.querySelector("#zt-profiles .zt-prof .zt-p-product");
      return JSON.stringify({tag: sel.tagName, val: sel.value,
        ownerList: !!document.querySelector('.zt-p-owner[list="zt-users"]'),
        dlOpts: dl.options.length});
    })()`, true);
    const si = JSON.parse(selInfo || "{}");
    check("④b 清单就位 → 产品下拉（保留7）+ 负责人挂账号下拉",
      si.tag === "SELECT" && si.val === "7" && si.ownerList === true && si.dlOpts === 2, selInfo);
    const layout = await evalJs(`(() => {
      const auto = document.querySelector(".zt-auto-options");
      const interval = document.querySelector(".zt-interval-field");
      const input = document.getElementById("zt-interval");
      const product = document.querySelector("#zt-profiles .zt-p-product");
      const ab = auto.getBoundingClientRect(), ib = interval.getBoundingClientRect();
      const nb = input.getBoundingClientRect(), pb = product.getBoundingClientRect();
      return JSON.stringify({ autoH: Math.round(ab.height), intervalH: Math.round(ib.height),
        numberInside: nb.left >= ib.left - 1 && nb.right <= ib.right + 1,
        productW: Math.round(pb.width), productVisible: pb.right <= innerWidth + 1,
        unit: (document.querySelector(".zt-iv-unit") || {}).textContent || "",
        ivVal: input.value, ivMin: input.getAttribute("min"),
        chips: document.querySelectorAll(".zt-auto-options > .toggle").length,
        chipH: Math.round((document.querySelector(".zt-auto-options > .toggle") || {getBoundingClientRect:()=>({height:0})}).getBoundingClientRect().height) });
    })()`, true);
    const ly = JSON.parse(layout || "{}");
    check("④b 自动化选项与扫描间隔成组对齐", ly.autoH < 100 && ly.numberInside, layout);
    check("④b 扫描间隔用分钟：老默认2h跟随新默认5分钟+最小值 5",
      ly.unit === "分钟" && ly.ivMin === "5" && ly.ivVal === "5", layout);
    check("④b 四个开关渲染为等高胶囊", ly.chips === 4 && ly.chipH >= 26 && ly.chipH <= 44, layout);
    check("④b 产品名称下拉有可读宽度且未溢出", ly.productW >= 180 && ly.productVisible, layout);
    await evalJs(`S.ztProducts = []; S.ztUsers = []; renderZentaoProfiles(); "ok"`);

    // ⑤ 立即扫描对不可达地址优雅报错
    await evalJs(`(() => {
      window.__ztnet = [];
      const orig = window.fetch.bind(window);
      window.fetch = (url, opts) => orig(url, opts).then((r) => {
        if (String(url).indexOf("/api/zentao") >= 0) {
          const c = r.clone();
          c.text().then((tx) => window.__ztnet.push(String(url) + " " + r.status + " " + tx.slice(0, 120)));
        }
        return r;
      });
      return 1;
    })()`);
    await evalJs(`document.getElementById("zt-base-url").value = "http://127.0.0.1:9";
      document.getElementById("zt-password").value = ""; saveZentao(); "ok"`);
    await sleep(600);
    await evalJs(`scanZentao(); "ok"`);
    const errShown = await waitFor(
      `document.getElementById("zentao-status").textContent.indexOf("最近错误") >= 0`, 15000);
    const zdump = await evalJs(`JSON.stringify({
      status: document.getElementById("zentao-status").textContent.slice(0, 220),
      lastErr: (S.zentao || {}).last_error || "",
      baseUrl: (S.zentao && S.zentao.config || {}).base_url,
      net: window.__ztnet || [] })`);
    check("⑤ 扫描不可达地址 → 最近错误上屏", errShown === true, zdump);

    console.log(results.every((r) => r.ok) ? "\n全部通过" : "\n存在失败项");
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  if (!results.every((r) => r.ok)) process.exit(1);
}

main().catch((e) => { console.error(e); process.exit(1); });
