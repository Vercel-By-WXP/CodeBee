# -*- coding: utf-8 -*-
"""分卷计划单测（不碰浏览器）：series-outline.md 解析 / 分卷.json 覆盖 /
章号→卷匹配 / 平台显示名去前缀。

跑法：python tests/test_publish_volumes.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="vols-test-")).resolve()
os.environ.setdefault("TUTTI_DATA", str(_TMP / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core.publish import volumes  # noqa: E402

FAILS = []


def check(name, fn):
    try:
        fn()
        print("  ok   %s" % name)
    except Exception as e:
        FAILS.append(name)
        print("  FAIL %s: %r" % (name, e))


def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expect failed")


OUTLINE = """# 《示例》八卷总纲

全书100章，三卷依次30、40、30章。每章2800—3200中文字。

## 第一卷：山门换锁

正文甲。

## 第二卷：县里有旧账

正文乙。

## 第三卷：试着开一道口

正文丙。
"""


def _wd(name, files):
    wd = _TMP / name
    wd.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        fp = wd / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding="utf-8")
    return wd


def test_parse_outline():
    wd = _wd("w1", {"大纲/series-outline.md": OUTLINE})
    plan = volumes.load_plan(str(wd))
    expect(len(plan) == 3, "三卷：%s" % plan)
    expect(plan[0] == {"no": 1, "name": "山门换锁", "from": 1, "to": 30},
           "卷一范围：%s" % plan[0])
    expect(plan[1]["from"] == 31 and plan[1]["to"] == 70, "卷二累计范围")
    expect(plan[2]["from"] == 71 and plan[2]["to"] == 100, "卷三累计范围")
    expect(volumes.name_for(str(wd), 49) == "县里有旧账",
           "49 章在卷二：%s" % volumes.name_for(str(wd), 49))
    expect(volumes.name_for(str(wd), 1) == "山门换锁", "1 章在卷一")
    expect(volumes.name_for(str(wd), 100) == "试着开一道口", "100 章在卷三")
    expect(volumes.name_for(str(wd), 101) == "", "越界无卷")


def test_json_override():
    wd = _wd("w2", {"分卷.json": json.dumps({"volumes": [
        {"name": "甲", "from": 1, "to": 5},
        {"name": "乙", "from": 6}]}, ensure_ascii=False)})
    plan = volumes.load_plan(str(wd))
    expect(len(plan) == 2 and plan[1]["to"] == 0, "json 覆盖、不设界：%s" % plan)
    expect(volumes.name_for(str(wd), 99) == "乙", "开区间命中")
    # volumes.json 别名同样认
    wd2 = _wd("w3", {"volumes.json": json.dumps(
        {"volumes": [{"name": "甲", "from": 1, "to": 9}]}, ensure_ascii=False)})
    expect(volumes.name_for(str(wd2), 3) == "甲", "别名文件")


def test_no_plan():
    wd = _wd("w4", {"连载说明.md": "没有卷计划"})
    expect(volumes.load_plan(str(wd)) == [], "无大纲无 json = 无计划")
    expect(volumes.name_for(str(wd), 3) == "", "无计划解析不出卷名")
    expect(volumes.load_plan(str(_TMP / "nope-wd")) == [], "目录不存在")
    # 章数与卷数对不上：宁可放弃不猜
    wd2 = _wd("w5", {"大纲/series-outline.md":
                     OUTLINE.replace("三卷依次30、40、30章", "三卷依次30、40章")})
    expect(volumes.load_plan(str(wd2)) == [], "章数不匹配不给计划")


def test_norm_and_volume_for():
    expect(volumes.norm_volume_name("第二卷：县里有旧账") == "县里有旧账", "去前缀")
    expect(volumes.norm_volume_name("县里有旧账") == "县里有旧账", "裸名原样")
    expect(volumes.norm_volume_name(" 第12卷: 名 ") == "名", "全角空格+半角冒号")
    plan = [{"no": 1, "name": "甲", "from": 1, "to": 10}]
    expect(volumes.volume_for(0, plan) is None, "章号 0 无卷")
    expect(volumes.volume_for(5, plan)["name"] == "甲", "界内命中")
    expect(volumes.volume_for(11, plan) is None, "界外无卷")
    expect(volumes.volume_for(5, []) is None, "空计划")


if __name__ == "__main__":
    print("== test_publish_volumes")
    check("parse_outline", test_parse_outline)
    check("json_override", test_json_override)
    check("no_plan", test_no_plan)
    check("norm_and_volume_for", test_norm_and_volume_for)
    print("== %d fail" % len(FAILS))
    sys.exit(1 if FAILS else 0)
