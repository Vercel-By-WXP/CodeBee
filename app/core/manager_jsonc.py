# -*- coding: utf-8 -*-
"""JSONC 片段改写：就地增删改目标键，保留注释与其余内容。

从 manager 拆出（单文件行数治理），manager 原样再导出：
  - _jsonc_scan_object：扫描对象边界与键表（跳过字符串转义与注释）；
  - _jsonc_strip_comments：读取前剥离注释；
  - _jsonc_set / _jsonc_delete / _jsonc_insert_entry：只动目标键的片段改写。
"""
from __future__ import annotations

import json
import re


# ---------------------------------------------------------------- JSONC 片段改写

def _jsonc_scan_object(text, start):
    """扫描 text[start]（须为 '{'）起的 JSON(C) 对象：返回 (闭合偏移, 键表)。
    键表为 [key, key_start, key_end, value_start, value_end]（偏移相对整个 text，
    value_end 指向值结束后一格）。跳过字符串转义、// 与 /* */ 注释、嵌套括号，
    未闭合时容错返回文末。"""
    n = len(text)
    i = start + 1
    keys = []
    state = "key"
    depth = 0

    def skip_string(j):
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return j + 1
            j += 1
        return n

    def skip_comment(j):
        if j + 1 < n and text[j + 1] == "/":
            e = text.find("\n", j)
            return n if e < 0 else e
        if j + 1 < n and text[j + 1] == "*":
            e = text.find("*/", j + 2)
            return n if e < 0 else e + 2
        return j + 1  # 非注释的孤立斜杠：当普通字符

    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "/":
            i = skip_comment(i)
            continue
        if state == "key":
            if ch == "}":
                return i, keys
            if ch == ",":
                i += 1
                continue
            if ch == '"':
                j = skip_string(i + 1)
                keys.append([text[i + 1:j - 1], i, j, 0, 0])
                state = "colon"
                i = j
                continue
            i += 1
            continue
        if state == "colon":
            if ch == ":":
                state = "value"
            i += 1
            continue
        # state == value
        if not keys:
            return n, keys  # 结构异常：放弃扫描
        keys[-1][3] = i
        if ch in "{[":
            depth = 0
            j = i
            while j < n:
                c = text[j]
                if c == '"':
                    j = skip_string(j + 1)
                    continue
                if c == "/":
                    j = skip_comment(j)
                    continue
                if c in "{[":
                    depth += 1
                elif c in "}]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            keys[-1][4] = j + 1
            i = j + 1
            state = "key"
        elif ch == '"':
            j = skip_string(i + 1)
            keys[-1][4] = j
            i = j
            state = "key"
        else:  # 数字 / true / false / null（或值后直接撞上 , } 换行的容错）
            j = i
            while j < n and text[j] not in ",}\r\n":
                j += 1
            keys[-1][4] = j
            # j==i 说明值位置直接是分隔符/换行（空值或状态残留）——必须前进一格，
            # 否则 while i < n 永远停在原地（死循环）
            i = j if j > i else j + 1
            state = "key"
    return n, keys


def _jsonc_strip_comments(text):
    """剥掉 jsonc 的 // 行注释与 /* */ 块注释，供 json.loads 解析。
    字符串字面量内部的 //（$schema 的 https:// 等）不剥——逐字符扫描；
    简单的 re.sub(r"//[^\\n]*") 会把 URL 截断导致解析失败。"""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i:min(j + 1, n)])
            i = j + 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            e = text.find("\n", i)
            i = n if e < 0 else e  # 行注释：换行符本身保留
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            e = text.find("*/", i + 2)
            i = n if e < 0 else e + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _jsonc_set(text, path, value_json):
    """JSON(C) 顶层就地写键：path=("provider","orch") 或 ("model",)。
    只动目标片段，其余文本（含注释与原格式）原样保留。返回 (new_text, ok)。
    空文件按空对象起笔（write_model 缺文件按需创建的产物）。"""
    brace = text.find("{")
    if brace < 0 and not text.strip():
        return ('{\n  "%s": %s\n}' % (path[0], value_json), True)
    if brace < 0:
        return text, False
    end, keys = _jsonc_scan_object(text, brace)
    head = path[0]
    if len(path) == 1:
        for _k, _ks, _ke, vs, ve in keys:
            if _k == head:
                return text[:vs] + value_json + text[ve:], True
        return _jsonc_insert_entry(text, brace, end, head, value_json), True
    # 二级路径：先定位一级键的值对象
    tgt = None
    for k, _ks, _ke, vs, ve in keys:
        if k == head:
            tgt = (vs, ve)
            break
    if tgt is None:  # 一级键不存在：整体插入
        block = json.dumps({path[1]: json.loads(value_json)}, ensure_ascii=False)
        return _jsonc_insert_entry(text, brace, end, head, block), True
    vs, ve = tgt
    if text[vs:ve].lstrip()[0:1] != "{":
        return text, False  # 一级值不是对象：保守放弃（不覆盖用户的非标结构）
    end2, keys2 = _jsonc_scan_object(text, vs)
    for k, _ks, _ke, v2s, v2e in keys2:
        if k == path[1]:
            return text[:v2s] + value_json + text[v2e:], True
    seg = text[vs:end2 + 1]
    new_seg = _jsonc_insert_entry(seg, 0, end2 - vs, path[1], value_json)
    if new_seg is None:
        return text, False
    return text[:vs] + new_seg + text[end2 + 1:], True


def _jsonc_delete(text, path):
    """Delete a JSON(C) object key while retaining surrounding comments/text."""
    if not path:
        return text, False
    brace = text.find("{")
    if brace < 0:
        return text, False
    end, keys = _jsonc_scan_object(text, brace)
    target = None
    if len(path) == 1:
        target = (brace, end, keys, path[0])
    else:
        hit = next((k for k in keys if k[0] == path[0]), None)
        if not hit or text[hit[3]:hit[4]].lstrip()[:1] != "{":
            return text, False
        end2, keys2 = _jsonc_scan_object(text, hit[3])
        target = (hit[3], end2, keys2, path[1])
    _start_obj, _end_obj, obj_keys, leaf = target
    hit = next((k for k in obj_keys if k[0] == leaf), None)
    if not hit:
        return text, False
    start, stop = hit[1], hit[4]
    # Prefer consuming the following comma; otherwise consume the preceding one.
    j = stop
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j < len(text) and text[j] == ",":
        stop = j + 1
    else:
        j = start - 1
        while j >= 0 and text[j] in " \t\r\n":
            j -= 1
        if j >= 0 and text[j] == ",":
            start = j
    return text[:start] + text[stop:], True


def _jsonc_insert_entry(text, brace, end, key, value_json):
    """在 {brace..end} 对象的开头插入 "key": value（带尾逗号，不依赖原文件的
    逗号风格）；对象为空时去掉多余逗号。"""
    m = re.match(r"\{([ \t\r\n]*)", text[brace:end + 1])
    first = brace + (m.end(1) if m else 1)  # m 从 brace 起 match：end(1) 已含 '{'
    if first >= end:  # 空对象 {}
        return text[:brace + 1] + ' "%s": %s ' % (key, value_json) + text[end:]
    return text[:first] + '"%s": %s,\n  ' % (key, value_json) + text[first:]
