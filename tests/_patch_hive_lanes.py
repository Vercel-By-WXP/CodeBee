# -*- coding: utf-8 -*-
"""蜂巢工作台升级：阶段泳道流水线 + running 秒表走动 + 尾巴去噪。

替换 app.js 中 renderHive/hiveTick 块（锚点：run.steps 注释行 → 蜂巢格点击注释行前）。
幂等：已含 hiveStage 则跳过。"""
import io
import subprocess
import sys
import time

P = "app/ui/app.js"

NEW_BLOCK = '''/* run.steps -> 阶段泳道流水线：规划→起草→评审→修订→打磨→合成，先后关系一眼可见；
 * 运行中蜜蜂摆动+秒表走动；轨道光点流向下一阶段；点格即看实时输出。 */
function hiveStage(role) {
  const r = String(role || "");
  if (/^(plan|outline)/.test(r)) return t("规划");
  if (/^draft/.test(r)) return t("起草");
  if (/critique/.test(r) || r === "review") return t("评审");
  if (/^(revise|fix)/.test(r)) return t("修订");
  if (/^polish/.test(r)) return t("打磨");
  if (/^(merge|verify)/.test(r)) return t("合成");
  if (/^implement/.test(r)) return t("实现");
  return t("执行");
}

/* running 卡片秒表：started_at HH:MM:SS → 走动计时（跨天/解析失败回退原值） */
function hiveElapsed(started) {
  const m = /^(\\d{2}):(\\d{2}):(\\d{2})$/.exec(String(started || ""));
  if (!m) return String(started || "");
  const now = new Date();
  const secs = Math.max(0, now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds()
    - (+m[1] * 3600 + +m[2] * 60 + +m[3]));
  const h = Math.floor(secs / 3600), mn = Math.floor((secs % 3600) / 60), sc = secs % 60;
  return (h ? h + ":" + String(mn).padStart(2, "0") : String(mn)) + ":" + String(sc).padStart(2, "0");
}

/* 尾巴去噪：codex 遥测 WARN/半行 JSON 不是"它在干什么"；JSONL agent_message 是正文 */
function hiveCleanLine(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = String(lines[i] || "").trim();
    if (!l) continue;
    if (/warn\\b|telemetry|metrics|failed to flush|mcp/i.test(l)) continue;
    if (l.startsWith("{") && /"type"\\s*:/.test(l)) {
      try {
        const ev = JSON.parse(l);
        if (ev.type === "item.completed" && ev.item && ev.item.type === "agent_message" && ev.item.text)
          return String(ev.item.text).slice(-160);
      } catch (e) { /* 半行 JSON 等下一轮 */ }
      continue;
    }
    return l.slice(-160);
  }
  return "";
}

window.renderHive = function (run) {
  const box = $("rd-hive");
  if (!box) return;
  const steps = (run && run.steps) || [];
  if (!run || !steps.length) { box.classList.add("hidden"); stopHiveTick(); return; }
  box.classList.remove("hidden");
  // 按步骤首次出现顺序分泳道（流水线天然有序）
  const lanes = [], byStage = {};
  steps.forEach((s) => {
    const stg = hiveStage(s.role);
    if (!byStage[stg]) { byStage[stg] = []; lanes.push(stg); }
    byStage[stg].push(s);
  });
  const running = steps.filter((s) => s.status === "running");
  const sub = $("rd-hive-sub");
  if (sub) sub.textContent = running.length
    ? t("在岗") + " " + running.length + " / " + steps.length : t("全部空闲");
  const activeIdx = lanes.map((l) => byStage[l].some((s) => s.status === "running")).lastIndexOf(true);
  const cell = (s) => {
    const st = s.status === "running" ? "running" : (s.status === "failed" ? "failed" : "done");
    const who = s.agent_label || s.agent || "";
    const title = [s.role, who, s.started_at ? t("开始于 ") + s.started_at : "",
                   (s.note ? "◆ " + s.note : ""), s.summary].filter(Boolean).join(" · ");
    const key = run.id + "|" + (s.log || "");
    return '<div class="hive-cell st-' + st + '" title="' + esc(title) + '" ' +
      'onclick="hiveOpenLog(\\'' + esc(run.id) + "', '" + esc(s.log || "") + '\\')">' +
      '<div class="hc-head">' +
      '<svg class="ico hc-bee" aria-hidden="true"><use href="#i-bee"></use></svg>' +
      '<span class="hc-role">' + esc(s.role || "") + "</span>" +
      '<span class="hc-who">' + esc(who) + "</span></div>" +
      '<div class="hc-tail" data-log="' + esc(s.log || "") + '">' +
      esc(hiveTails[key] || (s.summary || "")) + "</div>" +
      '<div class="hc-meta"><span class="hc-elapsed" data-started="' + esc(s.started_at || "") + '">' +
      (s.duration_s != null ? s.duration_s + "s" : hiveElapsed(s.started_at)) + "</span>" +
      (st === "running" ? '<span class="hc-live">● ' + t("工作中") + "</span>" : "") +
      "</div></div>";
  };
  const dots = (list) => list.slice(-12).map((s) =>
    '<i class="ld ld-' + (s.status === "running" ? "run" : s.status === "failed" ? "fail" : "done")
    + '" title="' + esc(s.role || "") + '"></i>').join("");
  $("rd-hive-cells").innerHTML = lanes.map((stage, i) => {
    const list = byStage[stage];
    const hasRun = list.some((s) => s.status === "running");
    const cls = hasRun ? "lane-active" : (i < activeIdx ? "lane-done" : "lane-idle");
    return '<div class="hive-lane ' + cls + '">' +
      '<div class="lane-head"><span class="lane-name">' + esc(stage) + '</span>' +
      '<span class="lane-n">×' + list.length + "</span>" +
      (hasRun ? '<span class="lane-live">● ' + t("进行中") + "</span>" : "") + "</div>" +
      '<div class="lane-track">' + dots(list) + "</div>" +
      '<div class="lane-cells">' + list.slice(-8).map(cell).join("") + "</div></div>";
  }).join('<div class="lane-flow" aria-hidden="true"></div>');
  const live = running.length && (run.status === "running" || run.status === "queued");
  if (hiveClock) { clearInterval(hiveClock); hiveClock = null; }
  if (live) {
    if (!hiveTimer) hiveTimer = setInterval(() => hiveTick(run.id), 2000);
    hiveTick(run.id);
    // 秒表走动：running 卡计时每秒刷新，页面一眼可见"在动"
    hiveClock = setInterval(() => {
      document.querySelectorAll("#rd-hive-cells .hc-elapsed[data-started]").forEach((el) => {
        el.textContent = hiveElapsed(el.dataset.started);
      });
    }, 1000);
    // 点开任务第一眼就在干活：自动展开第一个在岗步骤的实时日志。
    // 每 run 只自动开一次；用户手动收起后不再打扰。
    if (S.hiveAutoLog !== run.id && !currentLog) {
      const first = running.find((s) => s.log);
      if (first) { S.hiveAutoLog = run.id; toggleLog(run.id, first.log); }
    }
  } else stopHiveTick();
};

/* 活跃步骤的实时尾巴：拉 900 字符窗口，去噪后取最后一行正文 */
async function hiveTick(rid) {
  if (document.hidden || !rid) return;
  const box = $("rd-hive-cells");
  if (!box) return;
  const rels = Array.from(box.querySelectorAll(".hc-tail[data-log]"))
    .map((el) => el.dataset.log).filter(Boolean);
  for (const rel of rels) {
    try {
      const r = await api("/api/runs/" + encodeURIComponent(rid) +
        "/log?step=" + encodeURIComponent(rel) + "&tail=900");
      const lines = String(r.log || "").split("\\n").filter((l) => l.trim());
      const last = hiveCleanLine(lines);
      const key = rid + "|" + rel;
      if (last && hiveTails[key] !== last) {
        hiveTails[key] = last;
        box.querySelectorAll(".hc-tail").forEach((el) => {
          if (el.dataset.log === rel) el.textContent = last;
        });
      }
    } catch (e) { /* 网络抖动保留上一帧 */ }
  }
}

'''

ANCHOR_START = "/* run.steps -> 蜂巢格：运行中的排前且蜜蜂摆动；终态灰显可回看。轮询只对活跃 run 起。 */"
ANCHOR_END = "/* 蜂巢格点击 → 打开该步骤实时日志"


def healthy():
    r = subprocess.run(["node", "--check", P], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "")[-200:]


def main():
    # hiveClock 声明要加到 stopHiveTick 旁
    for i in range(10):
        ok, err = healthy()
        if not ok:
            print("wait %d: %s" % (i, err.replace("\n", " ")[:120]))
            time.sleep(20)
            continue
        s = io.open(P, encoding="utf-8").read()
        if "function hiveStage" in s:
            print("already")
            return 0
        if s.count(ANCHOR_START) != 1 or s.count(ANCHOR_END) != 1:
            print("anchor miss:", s.count(ANCHOR_START), s.count(ANCHOR_END))
            return 1
        a = s.index(ANCHOR_START)
        b = s.index(ANCHOR_END)
        s = s[:a] + NEW_BLOCK + s[b:]
        old_decl = "let hiveTimer = null;                    // 卡片尾巴轮询表"
        new_decl = ("let hiveTimer = null;                    // 卡片尾巴轮询表\n"
                    "let hiveClock = null;                    // running 秒表（1s 走动）")
        if s.count(old_decl) == 1:
            s = s.replace(old_decl, new_decl, 1)
        io.open(P, "w", encoding="utf-8", newline="\n").write(s)
        ok2, err2 = healthy()
        print("patched, syntax:", "OK" if ok2 else err2[:200])
        return 0 if ok2 else 1
    print("FAILED after retries")
    return 1


if __name__ == "__main__":
    sys.exit(main())
