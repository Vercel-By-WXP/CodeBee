# -*- coding: utf-8 -*-
"""微信读取 sidecar：由 64 位 Python ≥3.9（配 wechatauto-replica）运行，
把本机微信 4.x 群消息增量拉成本仓库已有的「导出 txt」格式。

为什么是独立进程：wechatauto 依赖链（winsdk 等）需要 64 位 Python ≥3.9，
而 CodeBee 服务钉在 3.8-32。sidecar 通过文件与主程序解耦——拉到的新消息
按「2024-01-01 12:34:56 昵称」行格式追加进监控文件夹，wxdigest 现有的
解析/增量游标/摘要/桌宠提醒整条链路零改动复用。

用法（两个模式，互斥）：
  列群（供前端选择）：
    python wx_reader.py --list-groups --result OUT.json
  拉增量（每次扫描前调用）：
    python wx_reader.py --pull --out DIR --state STATE.json \
                        --groups-file GROUPS.json --result OUT.json
    GROUPS.json = [{"username": "...@chatroom", "name": "群名"}, ...]

只读保证：只调用 get_groups/get_new_messages/get_group_members/get_self_info，
不调用任何发送/删除接口。
结果 JSON 写 --result 文件（stdout 被库日志污染，不作为契约）。
语法保持 3.8 兼容（测试解释器要 import 本文件的纯函数）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

# 群消息 content 自带「<wxid>: 前缀」，剥掉后用成员表翻译成昵称
_PREFIX_RE = re.compile(r"^([A-Za-z0-9_+@\-.]{3,}):\s*\n?")
_ILLEGAL_FN = re.compile(r'[\\/:*?"<>|]')

SELF_NAME = "我"


def sanitize_filename(name):
    """群名 → 合法文件名（Windows 非法字符换下划线；空名回落 username）。"""
    out = _ILLEGAL_FN.sub("_", str(name or "").strip())
    return out[:80] or "unnamed"


def strip_sender_prefix(content, sender_username):
    """群消息正文剥掉自带的「wxid: 」前缀（前缀与发送者一致才可信地剥）。"""
    text = str(content or "")
    m = _PREFIX_RE.match(text)
    if m and (not sender_username or m.group(1) == sender_username):
        return text[m.end():].strip()
    return text.strip()


def member_map(members):
    """get_group_members 结果 → {username: 显示名}。备注优先（微信 UI 同款）。"""
    out = {}
    for m in members or []:
        if not isinstance(m, dict):
            continue
        uid = m.get("username") or ""
        if not uid:
            continue
        out[uid] = m.get("remark") or m.get("nick_name") or uid
    return out


def format_line(ts_epoch, sender_name, text):
    """一条消息 → 导出格式行（时间戳行与正文之间不留空行，与现有解析器对齐）。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_epoch or 0))
    return "%s %s\n%s\n" % (stamp, sender_name or "?", str(text or "").strip())


def type_placeholder(mtype):
    """非文本消息 → 占位符（摘要时让模型知道有图/语音存在）。None=跳过不入正文。"""
    t = str(mtype or "")
    if "文本" in t:
        return None            # 文本走正文，不是占位符
    for key, ph in (("图片", "[图片]"), ("语音", "[语音]"), ("视频", "[视频]"),
                    ("文件", "[文件]"), ("链接", "[链接]"), ("名片", "[名片]"),
                    ("位置", "[位置]"), ("动画表情", "[表情]"), ("系统", None)):
        if key in t:
            return ph
    return "[%s]" % t if t and len(t) <= 8 else None


def _quiet_lib_logs():
    try:
        import logging
        logging.getLogger("wechatauto").setLevel(logging.WARNING)
    except Exception:
        pass


def _write_result(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_state(path):
    return _load_json(path, {}) or {}


def save_state(path, state):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def cmd_list_groups(result_path):
    _quiet_lib_logs()
    import wechatauto
    db = wechatauto.WeChatDB()
    groups, seen = [], set()
    for g in db.get_groups():
        if isinstance(g, dict) and g.get("username"):
            groups.append({"username": g["username"],
                           "name": g.get("name") or g["username"],
                           "member_count": str(g.get("member_count") or "")})
            seen.add(g["username"])
    # 新群兜底（2026-09-21 用户实测「缺一个新建的群」）：刚建的群可能还没
    # 落进 chat_room/contact，但只要有过消息就在会话列表里——合并补进，
    # 昵称未同步时先显示 wxid，微信同步后「刷新群列表」即见真名。
    try:
        for s in db.get_sessions(200):
            u = str((s or {}).get("username") or "")
            if u.endswith("@chatroom") and u not in seen:
                groups.append({"username": u,
                               "name": db.get_nickname(u) or u,
                               "member_count": ""})
                seen.add(u)
    except Exception:
        pass   # 会话库不可用不影响主清单
    _write_result(result_path, {"ok": True, "groups": groups, "count": len(groups)})


def cmd_pull(out_dir, state_path, groups_file, result_path):
    _quiet_lib_logs()
    import wechatauto
    sel = _load_json(groups_file, [])
    sel = [g for g in (sel or []) if isinstance(g, dict) and g.get("username")]
    state = load_state(state_path)
    os.makedirs(out_dir, exist_ok=True)
    db = wechatauto.WeChatDB()
    try:
        self_wxid = db.wxid
    except Exception:
        self_wxid = ""
    pulled, errors = {}, []
    for g in sel:
        username, name = g["username"], g.get("name") or g["username"]
        since = 0
        try:
            since = int((state.get(username) or {}).get("since_seq") or 0)
        except (TypeError, ValueError):
            since = 0
        try:
            msgs = db.get_new_messages(username, since_seq=since, limit=500)
        except Exception as e:
            errors.append("%s: %s" % (name, e))
            continue
        if not msgs:
            pulled[username] = 0
            continue
        members = {}
        try:
            members = member_map(db.get_group_members(username))
        except Exception:
            members = {}
        # 昵称缓存：退群成员不在成员表里，靠 get_nickname 兜底一次后永久缓存
        gstate = state.get(username) or {}
        names = dict(gstate.get("names") or {})

        def resolve(uid):
            if not uid:
                return "?"
            if self_wxid and uid == self_wxid:
                return SELF_NAME
            if uid in members:
                return members[uid]
            if uid in names:
                return names[uid]
            try:
                nick = str(db.get_nickname(uid) or "").strip()
            except Exception:
                nick = ""
            name2 = nick or uid
            if len(names) < 5000:          # 缓存上限防无限膨胀
                names[uid] = name2
            return name2

        fname = os.path.join(out_dir, sanitize_filename(name) + ".txt")
        header = "【%s】\n" % name
        need_header = not os.path.exists(fname)
        lines = []
        max_seq = since
        for m in msgs:
            seq = m.get("sort_seq") or 0
            if seq <= since:
                continue
            max_seq = max(max_seq, seq)
            uid = m.get("sender_username") or ""
            who = resolve(uid)
            mtype = m.get("type")
            text = None
            if "文本" in str(mtype or ""):
                text = strip_sender_prefix(m.get("content"), uid)
            else:
                text = type_placeholder(mtype)
            if not text:
                continue
            lines.append(format_line(m.get("create_time"), who, text))
        if lines:
            with open(fname, "a", encoding="utf-8") as f:
                if need_header:
                    f.write(header)
                f.write("\n".join(lines) + "\n")
        state[username] = {"name": name, "since_seq": max_seq,
                           "names": names, "t": time.time()}
        pulled[username] = len(lines)
    save_state(state_path, state)
    _write_result(result_path, {"ok": not errors, "pulled": pulled,
                                "errors": errors, "count": sum(pulled.values())})


def main():
    ap = argparse.ArgumentParser(description="CodeBee 微信群消息只读 sidecar")
    ap.add_argument("--list-groups", action="store_true")
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--state", default="")
    ap.add_argument("--groups-file", default="")
    ap.add_argument("--result", required=True, help="结果 JSON 落点（stdout 不可靠）")
    args = ap.parse_args()
    try:
        if args.list_groups:
            cmd_list_groups(args.result)
        elif args.pull:
            cmd_pull(args.out, args.state, args.groups_file, args.result)
        else:
            ap.error("需要 --list-groups 或 --pull")
    except Exception as e:
        # 顶层异常也落 result：微信未登录/DB 锁住等真实原因必须透传给主程序
        _write_result(args.result, {"ok": False, "errors": [str(e)],
                                    "groups": [], "pulled": {}, "count": 0})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
