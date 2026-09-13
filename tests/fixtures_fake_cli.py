# -*- coding: utf-8 -*-
"""测试夹具：一个假 CLI，把启动时的 cwd / argv / stdin 打到 stdout。

用于验证续会话时子进程确实在「会话所属项目目录」下启动（真实 CLI 需在
该目录下才能定位到会话）。输出由 runner 原样写入该步骤的日志文件，
测试再从日志里断言，夹具本身不碰文件系统。
仅测试使用，不参与产品逻辑。
"""
import os
import sys

print("cwd=%s" % os.getcwd())
print("argv=%s" % sys.argv[1:])
print("stdin=%s" % sys.stdin.read()[:60])
print("fake ok")
