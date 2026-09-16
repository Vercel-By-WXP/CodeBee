# -*- coding: utf-8 -*-
"""内存重建 planner.py：HEAD 内容 + 仅本次流式改动（剥掉并行代理的未提交改动）。
stdin=HEAD 内容，stdout=重建后内容。所有替换断言唯一命中，失败即非零退出。"""
import sys

b = sys.stdin.buffer.read().decode("utf-8")


def rep(old, new, want=1):
    global b
    n = b.count(old)
    assert n == want, "count %d != %d for %r" % (n, want, old[:60])
    b = b.replace(old, new)


# 1) time 导入
rep("import os\nimport re\n", "import os\nimport re\nimport time\n")

# 2) _append_log 之后插 _log_streamer（锚点带上 _append_log 特征行保证唯一）
anchor = '''            f.write(text if text.endswith("\\n") else text + "\\n")
    except OSError:
        pass
'''
streamer = '''            f.write(text if text.endswith("\\n") else text + "\\n")
    except OSError:
        pass


def _log_streamer(log_path, min_chars=400, min_secs=0.8):
    """直连流式增量 → 步骤日志的节流写入器（原样拼接，不额外加换行）。

    编排者直连生成原本全程黑箱：日志只有一行标题，用户盯着它几分钟以为
    卡死。每个 SSE 分片都开文件写太碎，攒够 min_chars 或超过 min_secs 才
    刷一次；结束时必须调 cb.flush() 补上尾段。无 log_path 时为空操作
    （仍带 flush，调用方无需判空）。"""
    if not log_path:
        def nop(delta):
            pass
        nop.flush = lambda: None
        return nop
    st = {"buf": "", "at": time.time()}

    def _write():
        if st["buf"]:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(st["buf"])
            except OSError:
                pass
            st["buf"] = ""
        st["at"] = time.time()

    def cb(delta):
        if not delta:
            return
        st["buf"] += delta
        if len(st["buf"]) >= min_chars or time.time() - st["at"] >= min_secs:
            _write()

    def flush():
        _write()
    cb.flush = flush
    return cb
'''
rep(anchor, streamer)

# 3) 连载大纲直连调用带流式回调
rep('''            res = modelhub.chat(prov["id"], model, prompt,
                                max_tokens=16000, timeout=300)''',
    '''            cb = _log_streamer(log_path)
            res = modelhub.chat(prov["id"], model, prompt,
                                max_tokens=16000, timeout=300, on_delta=cb)
            cb.flush()''')

# 4) code plan 直连调用带流式回调
rep('''def _orch_code_plan(task, prov, model, log_path=None):
    res = modelhub.chat(prov["id"], model,''',
    '''def _orch_code_plan(task, prov, model, log_path=None):
    cb = _log_streamer(log_path)
    res = modelhub.chat(prov["id"], model,''')

sys.stdout.buffer.write(b.encode("utf-8"))
