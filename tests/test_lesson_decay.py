# -*- coding: utf-8 -*-
"""弱教训闲置衰减（hippo-memory 借鉴，066 班深挖）单测。

语义：won==0 的教训随闲置时长衰减排序（30 天宽限 + 60 天线性爬满，封顶 0.15）；
won≥1 的被结局归因背书不衰减；无时间戳不衰减；衰减只压排序不删数据、且不跌进
失守证据带。跑法：python -m unittest discover -s tests -p "test_lesson_decay.py"
"""
from __future__ import annotations

from datetime import datetime, timedelta

from base import BaseTest


class LessonDecayTests(BaseTest):
    def _sk(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        return skills

    def _retitle(self, sk, lid, created_at, updated_at=None):
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == lid:
                    it["created_at"] = created_at
                    it["updated_at"] = updated_at or created_at
            sk._save(data)

    def test_stale_unproven_sinks_below_fresh_unproven(self):
        """判别性用例：陈旧未归因（hits 多）此前靠热度键排前；衰减后让位新鲜条目。"""
        sk = self._sk()
        stale = sk.upsert_lesson("code", "陈旧未验证", "A" * 6)
        fresh = sk.upsert_lesson("code", "新鲜未验证", "B" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == stale["id"]:
                    it["hits"] = 6                      # 热度更高
                    it["created_at"] = it["updated_at"] = \
                        (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        self.assertEqual(order[0], "新鲜未验证")         # 衰减把陈旧未验证压后
        self.assertEqual(order[-1], "陈旧未验证")

    def test_proven_old_lesson_not_decayed(self):
        """won≥1 的旧教训被结局归因背书，不衰减。"""
        sk = self._sk()
        proven = sk.upsert_lesson("code", "老而有效", "A" * 6)
        fresh_unproven = sk.upsert_lesson("code", "新鲜未验证", "B" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == proven["id"]:
                    it["won"], it["hits"] = 1, 3
                    it["created_at"] = it["updated_at"] = \
                        (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        self.assertEqual(order[0], "老而有效")

    def test_grace_period_no_decay(self):
        """宽限期内（≤30 天）未归因条目不衰减：热度键照常生效。"""
        sk = self._sk()
        ten_days = sk.upsert_lesson("code", "十天未验证", "A" * 6)
        fresh = sk.upsert_lesson("code", "刚建未验证", "B" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == ten_days["id"]:
                    it["hits"] = 5
                    it["created_at"] = it["updated_at"] = \
                        (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        self.assertEqual(order[0], "十天未验证")         # 宽限期内热度键照旧

    def test_decay_never_drops_into_loss_band(self):
        """衰减封顶 0.15：深度衰减的未验证条目仍排在真实失守者之上。"""
        sk = self._sk()
        stale = sk.upsert_lesson("code", "深度陈旧未验证", "A" * 6)
        lossy = sk.upsert_lesson("code", "反复失守", "B" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == stale["id"]:
                    it["created_at"] = it["updated_at"] = \
                        (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
                elif it["id"] == lossy["id"]:
                    it["lost"], it["hits"] = 3, 4        # karma≈0.333 < 0.35 衰减下限
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        self.assertEqual(order.index("深度陈旧未验证"), order.index("反复失守") - 1)

    def test_no_timestamp_no_decay(self):
        """无时间戳的条目（历史脏数据）karma 不衰减（无从判旧不臆测），但同分
        时按「新者优先」尾键排到带时间戳者之后——来源不明 = 排序最不信任。"""
        sk = self._sk()
        a = sk.upsert_lesson("code", "无时间戳", "A" * 6)
        b = sk.upsert_lesson("code", "正常条目", "B" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == a["id"]:
                    it.pop("created_at", None)
                    it.pop("updated_at", None)
                elif it["id"] == b["id"]:
                    it["created_at"] = it["updated_at"] = \
                        (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        # karma 都未衰减（同为中性 0.5）；尾键新者优先 → 无时间戳（ts=0 视为最老）垫底
        self.assertEqual(order[-1], "无时间戳")


if __name__ == "__main__":
    import unittest
    unittest.main()
