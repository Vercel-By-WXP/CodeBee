# -*- coding: utf-8 -*-
"""测试夹具：把指定环境变量回显到 stdout。

用于验证「供应商绑定的 env 确实注入了子进程」——真实 CLI 靠这些 env 拿
端点与密钥（如 dsh 读 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL）。
仅测试使用，不参与产品逻辑。
"""
import os
import sys

for name in (sys.argv[1:] or []):
    print("%s=%s" % (name, os.environ.get(name, "")))
