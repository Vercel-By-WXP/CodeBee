# -*- coding: utf-8 -*-
"""全量 discover 护栏（1022 配方）：selfupdate 的看门狗线程会 os._exit(0) 硬退
整个测试进程（exit 0 无汇总），测试期把 os._exit 换成 raise 让它变成可见失败。"""
import os


class _TestExitSignal(BaseException):
    pass


def _fake_exit(code=0):
    raise _TestExitSignal("os._exit(%s) blocked during tests" % code)


os._exit = _fake_exit
