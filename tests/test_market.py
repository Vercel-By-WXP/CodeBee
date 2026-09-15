# -*- coding: utf-8 -*-
"""插件市场（Market）测试：目录合法性与内容质量、安装真进 skills 库、幂等、
卸载边界（内置包/用户自建包不可动）、记录丢失自愈、多文件包。

市场只是 skills 之上的发现+安装层：装完的包必须能被 skills 正常解析与注入。
"""
from __future__ import annotations

import json

from base import BaseTest


def _reset_skills_cache():
    """skills 的用户包目录缓存归零（与 tests/base.py 同一手法，保证断言确定性）。"""
    from app.core import skills
    with skills._LOCK:
        skills._user_pack_cache.clear()
        skills._user_dir_mtime["ts"] = 0.0
        skills._user_dir_mtime["ids"] = None


class TestMarketCatalog(BaseTest):
    def runTest(self):
        from app.core import market, skills
        v = market.view()
        items = v["catalog"]
        self.assertTrue(items)
        self.assertEqual(len({x["id"] for x in items}), len(items))   # id 唯一

        # 1) skills 内置包进目录：已存在、不可安装、正文真实
        builtin = [x for x in items if x["builtin"]]
        self.assertEqual({x["id"] for x in builtin},
                         {p["id"] for p in skills.BUILTIN_PACKS})
        self.assertIn("fanqie-novel", {x["id"] for x in builtin})
        for x in builtin:
            self.assertTrue(x["installed"])
            self.assertFalse(x["installable"])
            self.assertTrue(x["desc"])
            self.assertGreaterEqual(x["chars"], 1000)

        # 2) 可安装包 4~6 个：分类必须取 skills 闭集枚举值，且覆盖多个不同分类
        installable = [x for x in items if x["installable"]]
        self.assertTrue(4 <= len(installable) <= 6)
        for x in installable:
            self.assertIn(x["category"],
                          skills.LESSON_CATEGORIES + [skills.LESSON_UNCATEGORIZED])
            self.assertTrue(x["desc"])
            self.assertFalse(x["installed"])
            self.assertGreaterEqual(x["chars"], 1500)                 # 内容真材实料
        self.assertGreaterEqual(len({x["category"] for x in installable}), 4)

        # 3) categories：闭集全枚举都在（UI 下拉不缺项）
        for cat in skills.LESSON_CATEGORIES:
            self.assertIn(cat, v["categories"])

        # 4) 包内容自带市场安装标记（装进用户库后靠它识别归属），无占位符
        for p in market.BUILTIN_PACKS:
            for rel, content in (p.get("files") or {}).items():
                self.assertTrue(content.strip(), p["id"])
                self.assertIn("source: market", content)
                self.assertIn("market_id: %s" % p["id"], content)
                low = content.lower()
                for bad in ("lorem", "todo", "待补充", "占位符"):
                    self.assertNotIn(bad, low)


class TestMarketInstallRemove(BaseTest):
    def runTest(self):
        from app.core import market, skills

        # 1) 安装：文件真落进 skills 用户库，被解析为用户包并按 scopes 注入
        res, err = market.install("git-workflow")
        self.assertIsNone(err)
        self.assertFalse(res["already"])
        target = self.data_dir / "skillpacks" / res["file"]
        self.assertTrue(target.is_file())
        self.assertTrue((self.data_dir / "market.json").is_file())    # 状态持久化
        _reset_skills_cache()
        ups = [p for p in skills.user_packs() if p["file"] == str(target)]
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0]["name"], "Git 提交与分支守则")
        self.assertTrue(ups[0]["user"])
        block, used = skills.block_for({"type": "code"})              # scopes=["*"]
        self.assertIn("Git 提交与分支守则", block)
        self.assertIn(ups[0]["id"], used)
        card = next(x for x in market.view()["catalog"] if x["id"] == "git-workflow")
        self.assertTrue(card["installed"])
        reg = json.loads((self.data_dir / "market.json").read_text(encoding="utf-8"))
        self.assertIn("git-workflow", reg["installed"])

        # 2) 幂等：再装一次 already=True，不产生重复文件/重复包
        res2, err2 = market.install("git-workflow")
        self.assertIsNone(err2)
        self.assertTrue(res2["already"])
        _reset_skills_cache()
        self.assertEqual(len(skills.user_packs()), 1)

        # 3) scopes 生效：小说包只在小说类流程注入（临时停用两个内置大包——
        #    它们合计已逼近 MAX_INJECT_CHARS，会把后面的注入块截断干扰断言）
        res3, err3 = market.install("character-bible")
        self.assertIsNone(err3)
        _reset_skills_cache()
        self.assertIsNone(skills.pack_op("qimao-signing", "disable"))
        self.assertIsNone(skills.pack_op("fanqie-novel", "disable"))
        try:
            self.assertIn("角色小传", skills.block_for({"type": "serial_novel"})[0])
            self.assertNotIn("角色小传", skills.block_for({"type": "code"})[0] or "")
        finally:
            self.assertIsNone(skills.pack_op("qimao-signing", "enable"))
            self.assertIsNone(skills.pack_op("fanqie-novel", "enable"))

        # 4) 未知 id 与内置包：install 报错；内置包 install/remove 都拒绝
        self.assertIsNotNone(market.install("ghost")[1])
        self.assertIsNotNone(market.install("fanqie-novel")[1])
        self.assertIsNotNone(market.remove("fanqie-novel"))
        builtin_txt = skills.pack_text(
            next(p for p in skills.BUILTIN_PACKS if p["id"] == "fanqie-novel"))
        self.assertGreater(len(builtin_txt), 1000)                    # 内置包原封不动
        self.assertIsNotNone(market.remove("ghost"))                  # 非市场包

        # 5) 用户自建包受保护：占住目标名的无标记文件绝不被覆盖，市场包换名避让
        self.assertIsNone(market.remove("git-workflow"))              # 先卸掉第 1 步装的
        updir = self.data_dir / "skillpacks"
        (updir / "market-git-workflow.md").write_text(
            "---\nname: 我自己的包\nnote: x\nscopes:\n- \"*\"\n---\n\n自己攒的私货\n",
            encoding="utf-8")
        res4, err4 = market.install("git-workflow")
        self.assertIsNone(err4)
        self.assertNotEqual(res4["file"], "market-git-workflow.md")   # 换名避让
        self.assertTrue((updir / "market-git-workflow.md").is_file())
        self.assertIn("自己攒的私货",
                      (updir / "market-git-workflow.md").read_text(encoding="utf-8"))
        _reset_skills_cache()
        names = [p["name"] for p in skills.user_packs()]
        self.assertIn("我自己的包", names)                             # 用户包仍在
        self.assertIn("Git 提交与分支守则", names)
        self.assertIsNone(market.remove("git-workflow"))              # 只删自己那份
        self.assertTrue((updir / "market-git-workflow.md").is_file())
        _reset_skills_cache()
        self.assertNotIn("Git 提交与分支守则",
                         [p["name"] for p in skills.user_packs()])

        # 6) 卸载后状态归零；再卸报错
        card = next(x for x in market.view()["catalog"] if x["id"] == "git-workflow")
        self.assertFalse(card["installed"])
        self.assertIsNotNone(market.remove("git-workflow"))

        # 7) 记录丢失自愈：market.json 被清掉，也能靠文件标记识别并卸载
        _, err5 = market.install("worldview-consistency")
        self.assertIsNone(err5)
        self.assertIn("worldview-consistency", market.installed_ids())
        (self.data_dir / "market.json").unlink()
        self.assertIn("worldview-consistency", market.installed_ids())
        card = next(x for x in market.view()["catalog"]
                    if x["id"] == "worldview-consistency")
        self.assertTrue(card["installed"])
        self.assertIsNone(market.remove("worldview-consistency"))
        self.assertNotIn("worldview-consistency", market.installed_ids())

        # 8) 多文件包：主文件进 skills 注入通道，附加件落 assets 且不被当作用户包
        fake = {"id": "test-multi", "name": "测试多文件包", "desc": "d",
                "category": "流程规范", "scopes": ["*"], "file": "nonexistent.md",
                "files": {"market-test-multi.md":
                          "---\nname: 测试多文件包\nnote: n\nscopes:\n- \"*\"\n"
                          "source: market\nmarket_id: test-multi\n---\n\n主文件正文",
                          "assets/extra.md": "随包附加资料"}}
        market.BUILTIN_PACKS.append(fake)
        try:
            res6, err6 = market.install("test-multi")
            self.assertIsNone(err6)
            self.assertTrue((updir / "market-test-multi.md").is_file())
            extra = updir / "market-assets" / "test-multi" / "assets" / "extra.md"
            self.assertTrue(extra.is_file())
            self.assertEqual(extra.read_text(encoding="utf-8"), "随包附加资料")
            _reset_skills_cache()
            names8 = [p["name"] for p in skills.user_packs()]
            self.assertIn("测试多文件包", names8)          # 主文件进 skills 注入通道
            self.assertEqual(names8.count("测试多文件包"), 1)  # 附加件没被当作用户包
            self.assertIsNone(market.remove("test-multi"))
            self.assertFalse((updir / "market-test-multi.md").exists())
            self.assertFalse((updir / "market-assets" / "test-multi").exists())
            _reset_skills_cache()
            self.assertNotIn("测试多文件包",
                             [p["name"] for p in skills.user_packs()])
        finally:
            market.BUILTIN_PACKS.pop()


if __name__ == "__main__":
    import unittest as _u
    _u.main()
