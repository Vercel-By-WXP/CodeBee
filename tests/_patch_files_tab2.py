# -*- coding: utf-8 -*-
"""成品 TAB 收尾：检查器数据源接 loadArtifacts、过期判断放宽、引用选中优先迷你框。"""
import io
import subprocess
import sys

JS = "app/ui/app.js"
CSS = "app/ui/style.css"


def main():
    s = io.open(JS, encoding="utf-8").read()
    changed = []

    # 1) drawInspector 渲染段接成品拉取（sig 早退之后才走到，files 名单变化才重画）
    old = """    S.inspTab = (active || g.state !== "isolated") ? "progress" : "git";
  }
  applyInspectorTab();"""
    new = """    S.inspTab = (active || g.state !== "isolated") ? "progress" : "git";
  }
  applyInspectorTab();
  loadArtifacts(runId);   // 成品 TAB：工作目录新产出（含实时预览刷新）"""
    if s.count(old) == 1:
        s = s.replace(old, new, 1)
        changed.append("drawInspector hook")

    # 2) 过期判断放宽：检查器当前的 run 也算有效目标
    old2 = """  // 用户可能已经切到别的详情：过期响应不落盘
  if (S.detailRunId !== runId && !(S.detailTaskKey && S.lastRun && S.lastRun.id === runId)) return;"""
    new2 = """  // 用户可能已经切到别的详情：过期响应不落盘（检查器当前的 run 同样有效）
  const inspRun = (S.inspData && S.inspData.run && S.inspData.run.id) || "";
  if (S.detailRunId !== runId && inspRun !== runId &&
      !(S.detailTaskKey && S.lastRun && S.lastRun.id === runId)) return;"""
    if s.count(old2) == 1:
        s = s.replace(old2, new2, 1)
        changed.append("stale guard")

    # 3) 引用选中：检查器打开时优先迷你指挥框
    old3 = '''  const ta = $("rd-msg-input");
  if (!text) { toast(t("请先在预览正文里选中一段文字"), true); return; }'''
    new3 = '''  const ta = (S.inspKey && $("insp-msg-input")) || $("rd-msg-input");
  if (!text) { toast(t("请先在预览正文里选中一段文字"), true); return; }'''
    if s.count(old3) == 1:
        s = s.replace(old3, new3, 1)
        changed.append("quote target")

    io.open(JS, "w", encoding="utf-8", newline="\n").write(s)

    c = io.open(CSS, encoding="utf-8").read()
    if ".insp-files .file-chips" not in c:
        c += """
/* 检查器「成品文件」TAB：窄栏竖排 chips + 目录行 */
.insp-files .files-head { display: flex; flex-direction: column; gap: 3px; margin-bottom: 8px; }
.insp-files .files-head .wd { max-width: 100%; }
.insp-files .file-chips { flex-direction: column; flex-wrap: nowrap; }
.insp-files .file-chip { max-width: 100%; }
.insp-files .file-chip.prev { justify-content: center; }
.insp-files .rd-preview { margin-top: 8px; }
"""
        io.open(CSS, "w", encoding="utf-8", newline="\n").write(c)
        changed.append("css")

    r = subprocess.run(["node", "--check", JS], capture_output=True, text=True)
    print(changed, "syntax:", r.returncode == 0, (r.stderr or "")[-120:])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
