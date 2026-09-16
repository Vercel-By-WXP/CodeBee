# -*- coding: utf-8 -*-
"""附件（截图/文件）与代码版本（git）单元测试：全部用临时目录 + 本地 git 仓库。"""
from __future__ import annotations

import base64
import json
import subprocess

from base import BaseTest


def _git(wd, *args):
    r = subprocess.run(["git", *args], cwd=str(wd), capture_output=True, text=True)
    return r


def _init_repo(wd):
    _git(wd, "init", "-q")
    _git(wd, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(wd, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init")
    _git(wd, "branch", "-M", "main")


class TestAttachmentPending(BaseTest):
    def runTest(self):
        from app.core import attachments
        png = base64.b64encode(b"\x89PNG fake image bytes").decode()
        meta = attachments.save_pending("界面 截图.png", png)
        self.assertEqual(meta["name"], "界面 截图.png")
        self.assertTrue(meta["mime"].startswith("image/"))
        self.assertEqual(len(meta["id"]), 16)
        # 非法：坏 base64 / 空内容 / 超限 / 非白名单扩展名 / 名字去路径化
        with self.assertRaises(ValueError):
            attachments.save_pending("x.png", "!!!not-base64!!!")
        with self.assertRaises(ValueError):
            attachments.save_pending("empty.png", base64.b64encode(b"").decode())
        with self.assertRaises(ValueError):
            attachments.save_pending("big.png", base64.b64encode(b"x" * (attachments.MAX_BYTES + 1)).decode())
        with self.assertRaises(ValueError):
            attachments.save_pending("evil.exe", base64.b64encode(b"MZ").decode())
        m2 = attachments.save_pending("..\\..\\etc\\passwd.txt", base64.b64encode(b"hi").decode())
        self.assertEqual(m2["name"], "passwd.txt")


class TestAttachmentCommit(BaseTest):
    def runTest(self):
        from app.core import attachments
        data = b"hello attachment"
        meta = attachments.save_pending("notes.md", base64.b64encode(data).decode())
        img = attachments.save_pending("shot.png", base64.b64encode(b"\x89PNG").decode())
        items = attachments.commit_to_workdir(str(self.workdir), [meta["id"], img["id"]])
        self.assertEqual(len(items), 2)
        p = self.workdir / "_attachments" / "notes.md"
        self.assertTrue(p.is_file())
        self.assertEqual(p.read_bytes(), data)
        self.assertEqual(items[0]["path"], "_attachments/notes.md")
        # context 块列出全部路径；image_paths 只返回图片
        blk = attachments.context_block(items)
        self.assertIn("_attachments/notes.md", blk)
        self.assertIn("_attachments/shot.png", blk)
        imgs = attachments.image_paths({"attachments": items}, str(self.workdir))
        self.assertEqual(len(imgs), 1)
        self.assertTrue(imgs[0].endswith("shot.png"))
        # 非法 id / 丢失文件静默跳过
        self.assertEqual(attachments.commit_to_workdir(str(self.workdir), ["zz", meta["id"]]), [])


class TestOfficeTextExtract(BaseTest):
    def runTest(self):
        import zipfile
        from app.core import attachments
        # 造一个最小 docx：word/document.xml
        docx = self.tmp / "t.docx"
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml",
                        '<w:document xmlns:w="u"><w:body>'
                        '<w:p><w:r><w:t>第一章 计划书</w:t></w:r></w:p>'
                        '<w:p><w:r><w:t>预算 100 万</w:t></w:r></w:p>'
                        '</w:body></w:document>')
        txt = attachments.extract_office_text(docx)
        self.assertIn("第一章 计划书", txt)
        self.assertIn("预算 100 万", txt)
        # 最小 xlsx：sharedStrings + workbook + 一个 sheet
        xlsx = self.tmp / "t.xlsx"
        with zipfile.ZipFile(xlsx, "w") as zf:
            zf.writestr("xl/workbook.xml",
                        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                        '<sheets><sheet name="预算" sheetId="1" r:id="r1"/></sheets></workbook>')
            zf.writestr("xl/_rels/workbook.xml.rels",
                        '<Relationships><Relationship Id="r1" '
                        'Target="worksheets/sheet1.xml"/></Relationships>')
            zf.writestr("xl/sharedStrings.xml",
                        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        '<si><t>项目名</t></si><si><t>金额</t></si></sst>')
            zf.writestr("xl/worksheets/sheet1.xml",
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        '<sheetData><row r="1"><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                        '<row r="2"><c><v>100</v></c><c><v>200</v></c></row></sheetData></worksheet>')
        txt2 = attachments.extract_office_text(xlsx)
        self.assertIn("工作表: 预算", txt2)
        self.assertIn("项目名	金额", txt2)
        self.assertIn("100	200", txt2)
        # pptx：两页幻灯片
        pptx = self.tmp / "t.pptx"
        with zipfile.ZipFile(pptx, "w") as zf:
            zf.writestr("ppt/slides/slide2.xml", '<p><a:t>第二页内容</a:t></p>')
            zf.writestr("ppt/slides/slide1.xml", '<p><a:t>第一页标题</a:t></p>')
        txt3 = attachments.extract_office_text(pptx)
        self.assertIn("幻灯片 1", txt3)
        self.assertIn("第一页标题", txt3)
        self.assertIn("第二页内容", txt3)
        self.assertLess(txt3.index("第一页标题"), txt3.index("第二页内容"))
        # DTD 实体包 → 拒绝解析但不崩（返回 None）
        evil = self.tmp / "evil.docx"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("word/document.xml",
                        b'<!DOCTYPE doc [<!ENTITY a "x">]><doc>&a;</doc>')
        self.assertIsNone(attachments.extract_office_text(evil))
        # 坏 zip → None
        bad = self.tmp / "bad.docx"
        bad.write_bytes(b"not a zip")
        self.assertIsNone(attachments.extract_office_text(bad))


class TestOfficeAttachmentCommit(BaseTest):
    def runTest(self):
        import zipfile
        from app.core import attachments, store
        # 新扩展名收得下（doc/rtf/ods 老格式直接落盘）
        for name in ("方案.doc", "说明.rtf", "表格.ods"):
            meta = attachments.save_pending(name, base64.b64encode(b"bin").decode())
            self.assertEqual(meta["name"], name)
        # docx 提交后生成伴生文本版，context 指向它
        docx = self.tmp / "pending-doc.docx"
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("word/document.xml",
                        '<w:document xmlns:w="u"><w:body><w:p><w:r><w:t>需求正文</w:t></w:r></w:p>'
                        '</w:body></w:document>')
        meta = attachments.save_pending(
            "需求.docx", base64.b64encode(docx.read_bytes()).decode())
        items = attachments.commit_to_workdir(str(self.workdir), [meta["id"]])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("text_path"), "_attachments/需求.docx.txt")
        side = self.workdir / "_attachments" / "需求.docx.txt"
        self.assertTrue(side.is_file())
        self.assertIn("需求正文", side.read_text(encoding="utf-8"))
        # context 块指引优先读文本版
        blk = attachments.context_block(items)
        self.assertIn("正文文本版见 _attachments/需求.docx.txt", blk)
        # 带任务走一遍 create_task 注入
        meta2 = attachments.save_pending(
            "需求2.docx", base64.b64encode(docx.read_bytes()).decode())
        t = store.create_task({"type": "code", "goal": "按 Word 需求实现",
                               "workdir": str(self.workdir),
                               "attachments": [meta2["id"]]})
        self.assertTrue(t["attachments"][0].get("text_path"))
        self.assertIn("_attachments/需求2.docx.txt", t["context"])


class TestAttachmentInCreateTask(BaseTest):
    def runTest(self):
        from app.core import attachments, store
        meta = attachments.save_pending("spec.md", base64.b64encode(b"spec body").decode())
        t = store.create_task({"type": "code", "goal": "按附件实现", "workdir": str(self.workdir),
                               "attachments": [meta["id"]]})
        self.assertEqual(t["attachments"][0]["path"], "_attachments/spec.md")
        self.assertIn("_attachments/spec.md", t["context"])
        self.assertTrue((self.workdir / "_attachments" / "spec.md").is_file())


class TestGitmodValidRev(BaseTest):
    def runTest(self):
        from app.core import gitmod
        self.assertTrue(gitmod.valid_rev("main"))
        self.assertTrue(gitmod.valid_rev("v1.0"))
        self.assertTrue(gitmod.valid_rev("HEAD~2"))
        self.assertTrue(gitmod.valid_rev("abc123"))
        for bad in ("", "-x", "a b", "a;rm", "x" * 201):
            self.assertFalse(gitmod.valid_rev(bad), bad)


class TestGitmodInfoAndCheckout(BaseTest):
    def runTest(self):
        from app.core import gitmod, store
        wd = self.workdir
        (wd / "a.txt").write_text("v1", encoding="utf-8")
        _init_repo(wd)
        _git(wd, "tag", "v1.0")
        (wd / "a.txt").write_text("v2", encoding="utf-8")
        _git(wd, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-aqm", "v2")

        info = gitmod.repo_info(str(wd))
        self.assertTrue(info["repo"])
        self.assertEqual(info["branch"], "main")
        self.assertFalse(info["dirty"])
        self.assertIn("main", info["branches"])
        self.assertIn("v1.0", info["tags"])
        self.assertTrue(any(c["subject"] == "v2" for c in info["recent"]))

        # 非仓库目录
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertFalse(gitmod.repo_info(str(plain))["repo"])

        # 检出到任务分支：HEAD 回到 v1.0，a.txt 内容为 v1
        task = store.create_task({"type": "code", "goal": "g", "workdir": str(wd)})
        ok, err, gi = gitmod.prepare_checkout(str(wd), "v1.0", task["id"])
        self.assertTrue(ok, err)
        self.assertEqual(gi["branch"], "codebee/" + task["id"])
        self.assertEqual((wd / "a.txt").read_text(encoding="utf-8"), "v1")

        # 脏工作区不再拒绝检出：stash 原样收起（_attachments 未跟踪文件不算脏、不收）
        (wd / "a.txt").write_text("dirty", encoding="utf-8")
        ok, err, gi2 = gitmod.prepare_checkout(str(wd), "main", task["id"])
        self.assertTrue(ok, err)
        self.assertTrue(gi2["stash"])
        self.assertFalse(gitmod.repo_info(str(wd))["dirty"])   # 用户改动已收起，工作区干净
        # 收尾切回基线分支并还原用户改动
        fin = gitmod.finalize_run(str(wd), gi2, "codebee: 测试收尾")
        self.assertTrue(fin["restored"], fin.get("restore_error"))
        self.assertEqual(fin["restore_error"], "")
        self.assertEqual((wd / "a.txt").read_text(encoding="utf-8"), "dirty")
        self.assertEqual(_git(wd, "stash", "list").stdout.strip(), "")
        # 恢复基线内容后，只剩附件未跟踪文件 → 无需 stash 直接放行
        (wd / "a.txt").write_text("v2", encoding="utf-8")
        (wd / "_attachments").mkdir()
        (wd / "_attachments" / "x.png").write_bytes(b"\x89PNG")
        ok, err, _ = gitmod.prepare_checkout(str(wd), "main", task["id"])
        self.assertTrue(ok, err)  # 只有附件未跟踪文件 → 放行

        # 不存在的版本
        ok, err, _ = gitmod.prepare_checkout(str(wd), "no-such-branch", task["id"])
        self.assertFalse(ok)
        self.assertIn("不存在", err)

        # 非法引用直接拒绝（不走 git）
        ok, err, _ = gitmod.prepare_checkout(str(wd), "-evil", task["id"])
        self.assertFalse(ok)


class TestGitRevInCreateTask(BaseTest):
    def runTest(self):
        from app.core import store
        wd = self.workdir
        _init_repo(wd)
        t = store.create_task({"type": "code", "goal": "g", "workdir": str(wd),
                               "git_rev": "main"})
        self.assertEqual(t["git_rev"], "main")
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "goal": "g", "workdir": str(wd),
                               "git_rev": "-evil"})
