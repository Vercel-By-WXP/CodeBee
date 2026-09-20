# -*- coding: utf-8 -*-
"""测试夹具：模仿真实 CLI 的终端噪声输出（ANSI 颜色 + \r 进度条 + 画线框 +
GBK 中文 + U+FFFD 不可解字节）。

2026-09-20 实测的 aider/opencode 病症：这些噪声顺着 generic 分支的
`out["text"] = res["stdout"]` 一路流进步骤摘要和评审解析，详情页卡片显示成
一片 ◆ / ─ 墙，错误串里露出 `[91m[1mError:`。夹具只往 stdout/stderr 写字节，
不做别的（不碰文件系统、不联网），供 test_cli_noise_clean 断言清洗链路。

退出码由 argv[1] 指定（默认 0）：非 0 用来验证「错误摘要」这条路径。
仅测试使用，不参与产品逻辑。
"""
import sys

CODE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else 0
PROMPT = sys.stdin.read() if not sys.stdin.isatty() else ""

# 1) ANSI 颜色/加粗 —— 浏览器里渲染成 `[91m[1mError:` 的元凶
sys.stdout.write("\x1b[91m\x1b[1mError: \x1b[0m模型不可用\n")
sys.stdout.flush()
# 2) \r 覆写进度条 —— 只应保留最终形态 "Done."
sys.stdout.write("Loading 10%\rLoading 60%\rDone.\n")
sys.stdout.flush()
# 3) 画线框（GBK 编码，模拟 aider 在中文 Windows 上的输出）——成串要折叠
sys.stdout.buffer.write(("─" * 80 + "\r\n").encode("gbk"))
sys.stdout.buffer.write("litellm.BadRequestError: LLM Provider NOT provided\r\n".encode("gbk"))
# 4) 不可解字节（0xFF 不是合法 UTF-8/GBK）——应变成一句人话而不是乱码墙
sys.stdout.buffer.write(b"\xff" * 24 + b"\r\n")
# 5) 正常结论：必须原样保留
sys.stdout.buffer.write("最终结论：本章节奏偏慢。\r\n".encode("gbk"))
sys.stdout.flush()

if CODE:
    sys.stderr.write("stderr: 退出码 %d 的收尾信息\n" % CODE)
    sys.stderr.flush()
sys.exit(CODE)
