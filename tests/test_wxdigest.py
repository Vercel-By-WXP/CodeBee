# -*- coding: utf-8 -*-
"""群摘要测试：导出解析、增量游标（同秒边界身份）、首窗不回溯、失败重试、
fire_due 节流、配置校验、view/seen、pet 喂食与 pet.py 纯函数。
模型调用全程打桩（_summarize / builtin_agent），不出网。"""
from __future__ import annotations

from base import BaseTest

TXT_HEAD = """【产品讨论群】
2024-09-01 10:00:05 张三
今天上线新版本，大家注意回归

2024/9/1 10:01 李四
收到，我盯支付流程
"""

TXT_TAIL = """2024年9月1日 10:05:00 张三
王五辛苦，记得带上数据
"""

# 无群名头：群名回落文件名主干（测增量逻辑用，断言都钉在文件名上）
TXT_NOHEAD = "2024-09-01 10:00:05 张三\n今天上线新版本\n\n2024/9/1 10:01 李四\n收到\n"
TXT_TAIL_NOHEAD = "2024年9月1日 10:05:00 张三\n王五辛苦，记得带上数据\n"


def _mk_export(dir_path, name, data, enc="utf-8"):
    p = dir_path / name
    p.write_bytes(data if isinstance(data, bytes) else str(data).encode(enc))
    return p


class WxDigestBase(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import wxdigest
        self.wx = wxdigest
        wxdigest._FILE = self.data_dir / "wxdigest.json"
        wxdigest._DIR = self.data_dir / "wxdigest"
        wxdigest._test_reset()
        self.watch = self.tmp / "wx-exports"
        self.watch.mkdir()


class TestParse(WxDigestBase):
    def test_three_date_formats_and_multiline(self):
        msgs = self.wx.parse_messages(TXT_HEAD + TXT_TAIL)
        self.assertEqual(3, len(msgs))
        self.assertEqual("2024-09-01 10:00:05", msgs[0]["ts"])
        self.assertEqual("张三", msgs[0]["sender"])
        self.assertEqual("2024-09-01 10:01:00", msgs[1]["ts"])
        self.assertEqual("2024-09-01 10:05:00", msgs[2]["ts"])
        self.assertTrue(msgs[0]["hash"])

    def test_group_header_and_stem_fallback(self):
        self.assertEqual("产品讨论群", self.wx.group_name_of("x.txt", TXT_HEAD))
        self.assertEqual("老友群", self.wx.group_name_of("老友群.txt", "没头"))
        self.assertEqual("A群", self.wx.group_name_of("x.txt", "群名：A群\n2024-01-01 10:00 甲\nhi"))

    def test_senderless_stamp_uses_first_content_line(self):
        txt = "2024-09-01 10:00\n李四：收到\n2024-09-01 10:01\n张三：好"
        msgs = self.wx.parse_messages(txt)
        self.assertEqual([("李四", "收到"), ("张三", "好")],
                         [(m["sender"], m["text"]) for m in msgs])

    def test_garbage_lines_ignored(self):
        msgs = self.wx.parse_messages("随手记的没有时间戳\n2024-13-99 10:00 假\n")
        self.assertEqual([], msgs)

    def test_read_gbk_export(self):
        _mk_export(self.watch, "GBK群.txt", "2024-09-01 10:00 甲\n你好".encode("gbk"))
        self.wx.save_config({"watch_dir": str(self.watch)})
        seen = []
        self.wx._summarize = lambda cfg, g, w, watch: seen.append(g) or ("ok", {})
        r = self.wx._poll(force=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(["GBK群"], seen)


class TestIncremental(WxDigestBase):
    def _stub_ok(self):
        calls = []

        def fake(cfg, group, window, watch):
            calls.append((group, len(window), window[0]["ts"], window[-1]["ts"]))
            return "摘要|%s|%d条" % (group, len(window)), {"model": "m", "provider_name": "p"}

        self.wx._summarize = fake
        return calls

    def test_first_scan_tail_window_only(self):
        body = "".join("2024-09-01 10:%02d:%02d 用户%d\n消息%d\n"
                       % (i // 60, i % 60, i, i) for i in range(30))
        _mk_export(self.watch, "增量群.txt", TXT_NOHEAD + body)
        self.wx.save_config({"enabled": True, "watch_dir": str(self.watch)})
        # save_config 对 max_chars 有 4000 下限钳制；这里要测窗口逻辑本身，直接设状态
        self.wx._STATE["config"]["max_chars"] = 200
        calls = self._stub_ok()
        r = self.wx._poll(force=True)
        self.assertEqual(1, r["made"])
        # max_chars=200（每条约 30 字符权重）：首窗只装尾部 ~6 条，不回溯头部历史
        g, n, t0, t1 = calls[0]
        self.assertEqual("增量群", g)
        self.assertLessEqual(n, 10)
        self.assertGreaterEqual(t0, "2024-09-01 10:00:10")

    def test_append_picks_only_new(self):
        _mk_export(self.watch, "追加群.txt", TXT_NOHEAD)
        self.wx.save_config({"enabled": True, "watch_dir": str(self.watch),
                             "max_chars": 12000})
        calls = self._stub_ok()
        self.wx._poll(force=True)
        self.assertEqual(1, len(calls))
        # 文件没变：不重摘
        self.wx._poll(force=True)
        self.assertEqual(1, len(calls))
        # 追加三条（同群、无头，群名键保持「追加群」）：只摘新的
        fp = self.watch / "追加群.txt"
        fp.write_bytes((TXT_NOHEAD + TXT_TAIL_NOHEAD +
                        "2024-09-01 11:00 李四\n新消息A\n"
                        "2024-09-01 11:01 王五\n新消息B\n").encode("utf-8"))
        r = self.wx._poll(force=True)
        self.assertEqual(1, r["made"])
        g, n, t0, t1 = calls[-1]
        # 增量 = TXT_TAIL(10:05) + 追加两条(11:00/11:01)，首窗两条不重摘
        self.assertEqual(("追加群", 3, "2024-09-01 10:05:00", "2024-09-01 11:01:00"),
                         (g, n, t0, t1))

    def test_same_second_boundary_not_dropped(self):
        txt = ("2024-09-01 10:00:00 甲\n一\n2024-09-01 10:00:00 乙\n二\n"
               "2024-09-01 10:00:00 丙\n三\n")
        msgs = self.wx.parse_messages(txt)
        # 游标钉在「乙」这条（同秒第 2 条）：增量必须从「丙」起，不漏不重
        cur = self.wx._cursor_from(msgs[1])
        new = self.wx._new_since(msgs, cur)
        self.assertEqual(["丙"], [m["sender"] for m in new])
        # 游标消息被裁掉：退回按时间过滤
        new2 = self.wx._new_since(msgs, {"last_ts": "2024-09-01 10:00:00",
                                         "sender": "鬼", "hash": "00000000"})
        self.assertEqual([], new2)

    def test_failure_keeps_cursor_and_retries(self):
        _mk_export(self.watch, "重试群.txt", TXT_HEAD)
        self.wx.save_config({"enabled": True, "watch_dir": str(self.watch)})

        def boom(cfg, group, window, watch):
            raise RuntimeError("模型挂了")

        self.wx._summarize = boom
        r = self.wx._poll(force=True)
        self.assertFalse(r["ok"])
        self.assertIn("模型挂了", r["error"])
        v = self.wx.view()
        self.assertEqual(0, len(v["digests"]))
        self.assertIn("模型挂了", v["last_error"])
        # 恢复后同一拍重试成功
        self._stub_ok()
        r = self.wx._poll(force=True)
        self.assertTrue(r["ok"] and r["made"] == 1)


class TestScheduleAndApi(WxDigestBase):
    def test_fire_due_gates(self):
        self.assertIsNone(self.wx.fire_due())          # 未启用：零开销 None
        _mk_export(self.watch, "调度群.txt", TXT_NOHEAD)
        self.wx.save_config({"enabled": True, "watch_dir": str(self.watch)})
        calls = self._stub_ok()
        r = self.wx.fire_due()                          # 到点：真扫
        self.assertEqual("", r.get("skipped"))
        self.assertEqual(1, len(calls))
        r2 = self.wx.fire_due()                         # 刚扫过：节流窗口内跳过
        self.assertEqual("not_due", r2["skipped"])

    def _stub_ok(self):
        calls = []

        def fake(cfg, group, window, watch):
            calls.append((group, len(window)))
            return "s", {"model": "m", "provider_name": "p"}

        self.wx._summarize = fake
        return calls

    def test_save_config_validation(self):
        with self.assertRaises(ValueError):
            self.wx.save_config({"watch_dir": "相对/路径"})
        # 间隔/字数走钳制不报错（zentao 同口径）：超界落边界值
        cfg = self.wx.save_config({"interval_minutes": 1})
        self.assertEqual(self.wx.INTERVAL_MIN, cfg["interval_minutes"])
        cfg = self.wx.save_config({"interval_minutes": 99999, "enabled": True})
        self.assertEqual(self.wx.INTERVAL_MAX, cfg["interval_minutes"])
        self.assertTrue(cfg["enabled"])
        cfg = self.wx.save_config({"max_chars": 1})
        self.assertEqual(4000, cfg["max_chars"])

    def test_view_seen_unseen_and_pet_digest(self):
        _mk_export(self.watch, "未读群.txt", TXT_NOHEAD)
        self.wx.save_config({"enabled": True, "watch_dir": str(self.watch)})
        self._stub_ok()
        self.wx._poll(force=True)
        v = self.wx.view()
        self.assertEqual(1, v["unseen"])
        self.assertEqual(1, len(v["digests"]))
        d = v["digests"][0]
        self.assertEqual("未读群", d["group"])
        self.assertEqual(2, d["count"])
        self.assertEqual("2024-09-01 10:01:00", d["to_ts"])
        pd = self.wx.pet_digest()
        self.assertEqual(1, pd["unseen"])
        self.assertEqual("未读群", pd["latest"]["group"])
        # 面板打开 → seen 清零；pet 喂食同步归零（但最新摘要仍在）
        v2 = self.wx.seen_clear()
        self.assertEqual(0, v2["unseen"])
        pd2 = self.wx.pet_digest()
        self.assertEqual(0, pd2["unseen"])
        self.assertIsNotNone(pd2["latest"])

    def test_scan_without_watch_dir(self):
        self.wx.save_config({"enabled": True})
        r = self.wx.scan_now()
        self.assertFalse(r["ok"])
        self.assertIn("监控文件夹", r["error"])


class TestPetPureFunctions(BaseTest):
    def test_parse_snapshot_digest_passthrough(self):
        import sys
        sys.path.insert(0, str(self._paths.APP_DIR))
        import pet
        snap = pet.parse_snapshot({
            "settings": {"pet_enabled": True, "pet_mode": "always"},
            "workers": {"running": 1}, "tasks": [],
            "digest": {"unseen": 3, "latest": {"group": "A", "text": "- 要点一\n- 要点二"}},
        })
        self.assertEqual(3, snap["digest"]["unseen"])
        self.assertEqual("A", snap["digest"]["latest"]["group"])
        bad = pet.parse_snapshot({"tasks": []})
        self.assertEqual(0, bad["digest"]["unseen"])
        self.assertIsNone(bad["digest"]["latest"])

    def test_digest_bubble_text(self):
        import sys
        sys.path.insert(0, str(self._paths.APP_DIR))
        import pet
        dg = {"latest": {"group": "产品群", "text": "- 一句话总览：发版顺利\n- 待办：无"}}
        s = pet.digest_bubble_text(dg, "zh")
        self.assertIn("产品群", s)
        self.assertIn("一句话总览", s)
        long_txt = "x" * 80
        s2 = pet.digest_bubble_text({"latest": {"group": "g", "text": long_txt}}, "zh")
        self.assertLessEqual(len(s2), 80)

    def test_tooltip_digest_line(self):
        import sys
        sys.path.insert(0, str(self._paths.APP_DIR))
        import pet
        snap = {"tasks": [], "digest": {"unseen": 2, "latest": None}}
        lines = pet.tooltip_lines(snap, "zh")
        self.assertIn("2", lines[0])
        self.assertEqual(1, len(lines))


if __name__ == "__main__":
    unittest.main()
