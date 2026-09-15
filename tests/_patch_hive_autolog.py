# -*- coding: utf-8 -*-
"""renderHive 增强：活跃任务自动展开第一个在岗步骤的实时日志（每 run 一次）。"""
import io
import subprocess
import sys
import time

P = "app/ui/app.js"

OLD = """  const live = running.length && (run.status === "running" || run.status === "queued");
  if (live) { if (!hiveTimer) hiveTimer = setInterval(() => hiveTick(run.id), 2500); hiveTick(run.id); }
  else stopHiveTick();
};"""

NEW = """  const live = running.length && (run.status === "running" || run.status === "queued");
  if (live) {
    if (!hiveTimer) hiveTimer = setInterval(() => hiveTick(run.id), 2500);
    hiveTick(run.id);
    // 点开任务第一眼就在干活：自动展开第一个在岗步骤的实时日志。
    // 每 run 只自动开一次；用户手动收起后不再打扰。
    if (S.hiveAutoLog !== run.id && !currentLog) {
      const first = running.find((s) => s.log);
      if (first) { S.hiveAutoLog = run.id; toggleLog(run.id, first.log); }
    }
  } else stopHiveTick();
};"""


def healthy():
    r = subprocess.run(["node", "--check", P], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "")[-200:]


def main():
    for i in range(10):
        ok, err = healthy()
        if not ok:
            print("wait %d: %s" % (i, err.replace("\n", " ")[:120]))
            time.sleep(20)
            continue
        s = io.open(P, encoding="utf-8").read()
        if "S.hiveAutoLog" in s:
            print("already")
            return 0
        if s.count(OLD) != 1:
            print("anchor miss:", s.count(OLD))
            return 1
        io.open(P, "w", encoding="utf-8", newline="\n").write(s.replace(OLD, NEW, 1))
        ok2, err2 = healthy()
        print("patched, syntax:", "OK" if ok2 else err2[:160])
        return 0 if ok2 else 1
    print("FAILED after retries")
    return 1


if __name__ == "__main__":
    sys.exit(main())
