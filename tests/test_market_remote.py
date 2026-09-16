# -*- coding: utf-8 -*-
"""外部插件目录（market_remote）测试：SSRF 防护、双格式清单解析、不适配预分类、
纯技能类白名单检查（脚本/钩子/MCP 拒绝）、zip 下载安装全链路（网络全 mock，
绝不外联）、幂等与卸载。

核心不变量：技能文本会被注入给智能体当守则，任何可执行件都装不进来；
安装落地后与内置市场包走同一套 market 标记/记账/卸载纪律。
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from base import BaseTest


def _mk_zip(files):
    """{相对路径: 内容(bytes|str)} → zip bytes。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, data in files.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            zf.writestr(rel, data)
    return buf.getvalue()


def _patch_fetch(module, responder):
    """responder(url) → bytes；同时把地址解析改成「全部公网」，让 https URL
    能过 SSRF 网关（真实 DNS/网络绝不触碰）。"""
    def fake_getaddrinfo(host, port, proto=0, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", port))]
    def fetcher(url, cap=None):
        return responder(url)
    return mock.patch.object(module, "_fetch", side_effect=fetcher), \
        mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo)


SKILL_MD = """---
name: Clean Commit
description: Write clean commit messages.
---

# Clean Commit

Write commit messages that explain why, not what.
"""

REF_MD = "# Reference\n\nDetail doc for the skill.\n"


class TestMarketRemoteSsrf(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        # 仅 https
        with self.assertRaises(ValueError):
            mr.assert_public_url("http://cdn.example.com/marketplace.json")
        with self.assertRaises(ValueError):
            mr.assert_public_url("ftp://cdn.example.com/x")
        # 环回 / 私有 / 保留地址拒绝（用字面 IP 主机，不做真实解析）
        for bad in ("https://127.0.0.1/x", "https://localhost/x",
                    "https://192.168.1.1/x", "https://10.0.0.2/x",
                    "https://172.16.0.1/x", "https://169.254.1.1/x",
                    "https://0.0.0.0/x", "https://224.0.0.1/x"):
            with self.assertRaises(ValueError, msg=bad):
                mr.assert_public_url(bad)
        # 合法公网 URL：解析交给 mock，形态校验通过
        with mock.patch("socket.getaddrinfo",
                        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            host, port = mr.assert_public_url("https://cdn.example.com/a/b.json")
        self.assertEqual((host, port), ("cdn.example.com", 443))


class TestMarketRemoteParse(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        src = {"id": "zcode", "name": "ZCode 官方"}
        # zcode zip 直链格式
        e = mr._normalize(src, {
            "name": "Clean Commit",
            "displayName_i18n": {"zh-CN": "整洁提交"},
            "description": "Skill for commits.",
            "description_i18n": {"zh-CN": "提交技能。"},
            "version": "0.1.0", "author": {"name": "A"}, "category": "developer-tools",
            "keywords": ["git"],
            "source": {"source": "url", "type": "zip", "url": "https://x.example.com/p.zip",
                       "sha256": "ab" * 32, "path": "clean-commit"},
        })
        self.assertEqual(e["id"], "remote-zcode-Clean-Commit")
        self.assertEqual(e["title"], "整洁提交")
        self.assertEqual(e["desc"], "提交技能。")
        self.assertEqual(e["install"]["kind"], "zip")
        self.assertEqual(e["install"]["sha256"], "ab" * 32)
        # anthropic git-subdir 格式
        e2 = mr._normalize({"id": "anthropic", "name": "Anthropic 生态"}, {
            "name": "docs-skill",
            "description": "Docs skill.",
            "source": {"source": "git-subdir", "url": "https://github.com/x/y.git",
                       "path": "plugins/docs", "ref": "v1.2.3"},
        })
        self.assertEqual(e2["install"]["kind"], "git")
        self.assertEqual(e2["install"]["ref"], "v1.2.3")
        self.assertEqual(e2["install"]["path"], "plugins/docs")
        # 不认识的来源 → unsupported（会被预分类拦下）
        e3 = mr._normalize(src, {"name": "weird", "source": {"source": "local"}})
        self.assertEqual(e3["install"]["kind"], "unsupported")
        self.assertIsNotNone(mr._compat_block(e3))
        # id 非法字符清洗（防止 name 里带 / 或中文破坏标记字符集）
        e4 = mr._normalize(src, {"name": "我的 插件/Pro"})
        self.assertRegex(e4["id"], r"\Aremote-zcode-[\w.-]+\Z")

        # 仓库相对路径来源（anthropics/skills、社区技能库的格式）：
        # source 是字符串 → git 克隆清单所属仓库；entry.skills 变白名单
        repo_src = {"id": "anthropic-skills", "name": "A 官方技能",
                    "repo": "https://github.com/anthropics/skills.git"}
        e5 = mr._normalize(repo_src, {
            "name": "document-skills", "description": "docs",
            "source": "./", "skills": ["./skills/xlsx", "./skills/docx"],
        })
        self.assertEqual(e5["install"]["kind"], "git")
        self.assertEqual(e5["install"]["url"], "https://github.com/anthropics/skills.git")
        self.assertEqual(e5["install"]["path"], "")
        self.assertEqual(e5["install"]["skills"], ["xlsx", "docx"])
        # 子目录相对路径去壳
        e6 = mr._normalize(repo_src, {"name": "eng", "source": "./engineering"})
        self.assertEqual(e6["install"]["path"], "engineering")
        # 无 repo 元数据时相对路径没法装 → unsupported
        e7 = mr._normalize(src, {"name": "rel", "source": "./x"})
        self.assertEqual(e7["install"]["kind"], "unsupported")

        # ClawHub 来源：slug + reference，zip 直下
        e8 = mr._normalize(src, {
            "name": "context-budget", "displayName": "Context Budget",
            "summary": "Measure agent context.",
            "source": {"source": "clawhub", "slug": "context-budget",
                       "reference": "someone/context-budget"},
        })
        self.assertEqual(e8["install"]["kind"], "clawhub")
        self.assertEqual(e8["install"]["slug"], "context-budget")
        self.assertEqual(e8["install"]["reference"], "someone/context-budget")


class TestMarketRemoteCompat(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        def mk(**kw):
            raw = {"name": "n", "description": "d",
                   "source": {"type": "zip", "url": "https://x.example.com/a.zip"}}
            raw.update(kw)
            return mr._normalize({"id": "zcode", "name": "Z"}, raw)
        # 剥离式安装：脚本/钩子/MCP 关键词不再灰显（安装时自动剥离）
        self.assertIsNone(mr._compat_block(mk(keywords=["git", "cli"])))
        self.assertIsNone(mr._compat_block(mk(keywords=["mcp"])))
        self.assertIsNone(mr._compat_block(mk(keywords=["hooks"])))
        self.assertIsNone(mr._compat_block(mk(keywords=["slash-commands"])))
        self.assertIsNone(mr._compat_block(mk(description="Uses MCP servers.")))
        self.assertIsNone(mr._compat_block(mk(name="mcp-helper")))
        self.assertIsNone(mr._compat_block(mk(description="Webhook relay skill.")))
        # 只有来源类型不支持才灰显
        self.assertIsNotNone(mr._compat_block(mk(name="weird", source={"source": "local"})))


class TestMarketRemoteInspect(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        root = self.tmp / "plug"
        # 纯技能：skills/*/SKILL.md + 文档 + 图片 + json → 全保留，无剔除
        (root / "skills" / "foo").mkdir(parents=True)
        (root / "skills" / "foo" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        (root / "skills" / "foo" / "ref.md").write_text(REF_MD, encoding="utf-8")
        (root / "skills" / "foo" / "diagram.png").write_bytes(b"\x89PNG")
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
        files, stripped = mr.inspect_tree(root)
        self.assertEqual(stripped, [])
        self.assertEqual(len(files), 4)
        sk = mr.find_skills(root, files)
        self.assertEqual([s["name"] for s in sk], ["foo"])
        self.assertEqual(len(sk[0]["extras"]), 1)   # ref.md 是文本附件；png 不算

        # 剥离式：脚本目录 / mcp 配置 / 可执行扩展 / 未知扩展 → 剔除但技能保留
        for name, make, want in [
            ("scripts", lambda r: (r / "scripts").mkdir() or (r / "scripts" / "run.py").write_text("x", encoding="utf-8"), "scripts/run.py"),
            ("mcp", lambda r: (r / ".mcp.json").write_text("{}", encoding="utf-8"), ".mcp.json"),
            ("exec", lambda r: (r / "tools").mkdir() or (r / "tools" / "a.sh").write_text("x", encoding="utf-8"), "tools/a.sh"),
            ("binary", lambda r: (r / "lib.dll").write_bytes(b"MZ"), "lib.dll"),
            ("weird", lambda r: (r / "x.foo").write_text("x", encoding="utf-8"), "x.foo"),
        ]:
            r2 = self.tmp / ("plug-" + name)
            (r2 / "skills" / "foo").mkdir(parents=True)
            (r2 / "skills" / "foo" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
            make(r2)
            files2, stripped2 = mr.inspect_tree(r2)
            self.assertEqual(stripped2, [want], name)
            self.assertEqual([f[0] for f in files2], ["skills/foo/SKILL.md"], name)

        # github 型真实形态：skills/ 十个技能 + scripts/secret_sync.py + README
        # → 脚本剥离、技能全保留
        r3 = self.tmp / "plug-gh"
        (r3 / "scripts").mkdir(parents=True)
        (r3 / "scripts" / "secret_sync.py").write_text("x", encoding="utf-8")
        (r3 / "skills" / "commit").mkdir(parents=True)
        (r3 / "skills" / "commit" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        files3, stripped3 = mr.inspect_tree(r3)
        self.assertEqual(stripped3, ["scripts/secret_sync.py"])
        self.assertEqual([f[0] for f in files3], ["skills/commit/SKILL.md"])

        # skills 白名单：仓库里两个技能，条目只声明一个 → 只装声明的那个；
        # 未声明技能目录里的脚本不参与（既不剥离也不装）
        root3 = self.tmp / "plug-wl"
        for sk in ("one", "two"):
            (root3 / "skills" / sk).mkdir(parents=True)
            (root3 / "skills" / sk / "SKILL.md").write_text(
                SKILL_MD.replace("Clean Commit", "S-" + sk), encoding="utf-8")
        (root3 / "skills" / "two" / "helper.py").write_text("import os", encoding="utf-8")
        entry_wl = {"id": "remote-t-wl", "title": "WL", "desc": "",
                    "install": {"skills": ["one"]}}
        files, blocked, stripped = mr.build_files(entry_wl, root3)
        self.assertIsNone(blocked)
        self.assertEqual(stripped, [])
        tops = [k for k in files if "/" not in k]
        self.assertEqual(len(tops), 1)
        self.assertIn("S-one", files[tops[0]])
        self.assertNotIn("S-two", files[tops[0]])
        # 白名单与内容不匹配 → 明确报错
        entry_bad = {"id": "remote-t-wl2", "title": "WL2", "desc": "",
                     "install": {"skills": ["nope"]}}
        files, blocked, stripped = mr.build_files(entry_bad, root3)
        self.assertTrue(blocked)
        # 白名单技能自己带脚本 → 剥离脚本、技能照装（不连坐 = 剥离不拒收）
        root4 = self.tmp / "plug-wl2"
        (root4 / "skills" / "dirty").mkdir(parents=True)
        (root4 / "skills" / "dirty" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        (root4 / "skills" / "dirty" / "run.py").write_text("import os", encoding="utf-8")
        entry_d = {"id": "remote-t-wl3", "title": "WL3", "desc": "",
                   "install": {"skills": ["dirty"]}}
        files, blocked, stripped = mr.build_files(entry_d, root4)
        self.assertIsNone(blocked)
        self.assertEqual(stripped, ["skills/dirty/run.py"])
        self.assertTrue(any("S-main" in v or "Clean Commit" in v for v in files.values()))


class TestMarketRemoteInstall(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        from app.core import market, skills

        sha = __import__("hashlib").sha256(
            _mk_zip({"myplugin/skills/clean/SKILL.md": SKILL_MD,
                     "myplugin/skills/clean/ref.md": REF_MD,
                     "myplugin/.claude-plugin/plugin.json": "{}",
                     "myplugin/README.md": "# read"})).hexdigest()
        manifest = {"name": "zcode-plugins-official", "plugins": [{
            "name": "clean-commit", "description": "Skill for commits.",
            "source": {"source": "url", "type": "zip",
                       "url": "https://cdn.example.com/clean/0.1.0/plugin.zip",
                       "sha256": sha, "path": "clean"},
        }]}
        cache = self.data_dir / "market_remote"
        cache.mkdir(parents=True)
        (cache / "zcode.json").write_text(json.dumps(
            {"fetched_at": "2026-09-15 10:00:00", "url": "https://x/marketplace.json",
             "catalog": manifest}, ensure_ascii=False), encoding="utf-8")

        zip_bytes = _mk_zip({"myplugin/skills/clean/SKILL.md": SKILL_MD,
                             "myplugin/skills/clean/ref.md": REF_MD,
                             "myplugin/.claude-plugin/plugin.json": "{}",
                             "myplugin/README.md": "# read"})
        fetcher, resolver = _patch_fetch(mr, lambda url: zip_bytes)
        with fetcher, resolver:
            res, err = mr.install_remote("remote-zcode-clean-commit")
        self.assertIsNone(err, err)
        self.assertEqual(res["skills"], 1)

        # 落地：主文件带 market 标记，附件落 assets，记账含 remote 信息
        primary = self.data_dir / "skillpacks" / "market-remote-zcode-clean-commit.md"
        self.assertTrue(primary.is_file())
        head = primary.read_text(encoding="utf-8")[:400]
        self.assertIn("source: market", head)
        self.assertIn("market_id: remote-zcode-clean-commit", head)
        self.assertIn("# Clean Commit", primary.read_text(encoding="utf-8"))  # 正文原样
        self.assertTrue((self.data_dir / "skillpacks" / "market-assets"
                         / "remote-zcode-clean-commit" / "skills" / "clean" / "ref.md").is_file())
        rec = market._load_registry()["installed"]["remote-zcode-clean-commit"]
        self.assertEqual(rec["remote"]["name"], "clean-commit")
        self.assertIn("remote-zcode-clean-commit", market.installed_ids())

        # skills 能把它当正常用户包解析（frontmatter 合法）
        meta, body = skills._parse_frontmatter(primary.read_text(encoding="utf-8"))
        self.assertEqual(meta.get("name"), "Clean Commit")
        self.assertIn("*", meta.get("scopes") or [])

        # 幂等：重复安装 already=True，且不炸
        fetcher, resolver = _patch_fetch(mr, lambda url: zip_bytes)
        with fetcher, resolver:
            res2, err2 = mr.install_remote("remote-zcode-clean-commit")
        self.assertIsNone(err2)
        self.assertTrue(res2["already"])

        # view()：条目在、已装、可安装
        v = mr.view()
        self.assertEqual(v["total"], 1)
        e = v["entries"][0]
        self.assertTrue(e["installed"])
        self.assertTrue(e["installable"])
        self.assertEqual(v["sources"][0]["count"], 1)

        # 卸载：主文件与 assets 一起清掉
        self.assertIsNone(mr.remove_remote("remote-zcode-clean-commit"))
        self.assertFalse(primary.exists())
        self.assertNotIn("remote-zcode-clean-commit", market.installed_ids())


class TestMarketRemoteGuards(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr

        def seed(plugins):
            cache = self.data_dir / "market_remote"
            cache.mkdir(parents=True, exist_ok=True)
            for sid in ("zcode",):
                (cache / ("%s.json" % sid)).write_text(json.dumps(
                    {"fetched_at": "", "url": "", "catalog": {"plugins": plugins}},
                    ensure_ascii=False), encoding="utf-8")

        # 未知 id
        res, err = mr.install_remote("remote-zcode-nope")
        self.assertIsNone(res)
        self.assertIn("没有这个插件", err)

        # 来源类型不支持（unsupported）的条目：预分类灰显，安装直接拒（不发网络请求）
        seed([{"name": "local-only", "description": "x",
               "source": {"source": "local", "path": "x"}}])
        res, err = mr.install_remote("remote-zcode-local-only")
        self.assertIsNone(res)
        self.assertIn("来源类型不支持", err)

        # sha256 不符
        good = {"name": "clean", "description": "x",
                "source": {"type": "zip", "url": "https://cdn.example.com/c.zip",
                           "sha256": "ab" * 32}}
        seed([good])
        fetcher, resolver = _patch_fetch(mr, lambda url: _mk_zip({"skills/s/SKILL.md": SKILL_MD}))
        with fetcher, resolver:
            res, err = mr.install_remote("remote-zcode-clean")
        self.assertIsNone(res)
        self.assertIn("sha256", err)

        # 包里藏脚本：剥离式安装——脚本被剔除、技能照装，结果带 stripped 清单
        good2 = {"name": "dirty", "description": "x",
                 "source": {"type": "zip", "url": "https://cdn.example.com/d.zip"}}
        seed([good2])
        dirty = _mk_zip({"skills/s/SKILL.md": SKILL_MD, "scripts/go.py": "import os"})
        fetcher, resolver = _patch_fetch(mr, lambda url: dirty)
        with fetcher, resolver:
            res, err = mr.install_remote("remote-zcode-dirty")
        self.assertIsNone(err, err)
        self.assertEqual(res["stripped"], ["scripts/go.py"])
        self.assertEqual(res["skills"], 1)
        # 剔除的脚本确实没落盘
        self.assertFalse((self.data_dir / "skillpacks" / "market-assets"
                          / "remote-zcode-dirty" / "scripts" / "go.py").exists())

        # zip 路径穿越
        seed([dict(good2, name="slip")])
        slip = _mk_zip({"../evil.txt": "x", "skills/s/SKILL.md": SKILL_MD})
        fetcher, resolver = _patch_fetch(mr, lambda url: slip)
        with fetcher, resolver:
            res, err = mr.install_remote("remote-zcode-slip")
        self.assertIsNone(res)
        self.assertIn("可疑", err)

        # Windows 反斜杠成员名（Go archive/zip 打包的真实形态，ClawHub 实测）：
        # 解包不炸、路径归一、技能装上。py3.8 zipfile 写入时会强制转斜杠，
        # 造不出字面反斜杠包——用假 zip 对象直测 _safe_extract 的成员名处理。
        class FakeZI:
            def __init__(self, name, data):
                self.filename = name
                self.data = data.encode("utf-8")
                self.file_size = len(self.data)
            def is_dir(self):
                return False

        class FakeZip:
            def __init__(self, members):
                self._m = [FakeZI(n, d) for n, d in members]
            def infolist(self):
                return self._m
            def open(self, zi):
                return io.BytesIO(zi.data)

        dst = Path(tempfile.mkdtemp()) / "bs"
        mr._safe_extract(FakeZip([("skills\\s\\SKILL.md", SKILL_MD),
                                  ("skills\\s\\ref.md", REF_MD)]), dst)
        self.assertTrue((dst / "skills" / "s" / "SKILL.md").is_file())
        self.assertTrue((dst / "skills" / "s" / "ref.md").is_file())

        # 穿越换皮（反斜杠夹带 ..）仍然拒绝
        with self.assertRaises(ValueError):
            mr._safe_extract(FakeZip([("skills\\..\\evil.txt", "x")]), Path(tempfile.mkdtemp()) / "ev")

        # 无技能的纯文档包（剥离后没有 SKILL.md）
        seed([dict(good2, name="nodoc")])
        fetcher, resolver = _patch_fetch(mr, lambda url: _mk_zip({"README.md": "# x"}))
        with fetcher, resolver:
            res, err = mr.install_remote("remote-zcode-nodoc")
        self.assertIsNone(res)
        self.assertIn("未找到技能", err)


class TestMarketRemoteRefresh(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        manifest = {"name": "z", "plugins": [
            {"name": "a", "description": "x",
             "source": {"type": "zip", "url": "https://cdn.example.com/a.zip"}},
            {"name": "b", "description": "y",
             "source": {"type": "zip", "url": "https://cdn.example.com/b.zip"},
             "keywords": ["mcp"]},
        ]}
        # 只刷 zcode 一个来源（mock 对任何 URL 都返回同一份清单）
        fetcher, resolver = _patch_fetch(mr, lambda url: json.dumps(manifest).encode("utf-8"))
        with fetcher, resolver:
            v = mr.refresh(source_id="zcode")
        self.assertTrue(v["refresh"][0]["ok"])
        self.assertEqual(v["total"], 2)
        kinds = {e["name"]: e["compat"] for e in v["entries"]}
        self.assertEqual(kinds["a"], "ok")
        self.assertEqual(kinds["b"], "ok")   # topics 带 mcp → 剥离式可装
        # 缓存落盘：断网后 view() 仍可读（UI 加载永不联网）
        f = self.data_dir / "market_remote" / "zcode.json"
        self.assertTrue(f.is_file())
        v2 = mr.view()
        self.assertEqual(v2["total"], 2)
        # 坏清单：缺 plugins → 报 ok=False 而不是抛异常
        fetcher, resolver = _patch_fetch(mr, lambda url: b'{"name": "z"}')
        with fetcher, resolver:
            v3 = mr.refresh(source_id="zcode")
        self.assertFalse(v3["refresh"][0]["ok"])


class TestFetchProxyFallback(BaseTest):
    """_fetch 两段式网络策略：默认通道（系统代理）失败 → 自动直连重试；
    体量超限是确定性错误，不触发重试。用数字 IP 免 DNS。"""

    def runTest(self):
        from app.core import market_remote as mr
        import urllib.error

        url = "https://93.184.216.34/x.bin"
        mr.assert_public_url(url)   # 数字 IP 本地解析，同时验证 SSRF 网关放行

        calls = []

        class FakeResp:
            def __init__(self, data):
                self.data = data
            def read(self, n):
                d, self.data = self.data, b""
                return d
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def proxied_boom(req, timeout=None):
            calls.append("proxied")
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

        class DirectOpener:
            def open(self, req, timeout=None):
                calls.append("direct")
                return FakeResp(b"hello")

        # 代理 404 → 直连成功
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=proxied_boom), \
                mock.patch.object(mr.urllib.request, "build_opener",
                                  return_value=DirectOpener()) as bo:
            data = mr._fetch(url, cap=100)
        self.assertEqual(data, b"hello")
        self.assertEqual(calls, ["proxied", "direct"])
        bo.assert_called_once()          # 直连 opener 必须带 ProxyHandler({})

        # 体量超限：ValueError 直抛，不重试
        calls.clear()
        with mock.patch.object(mr.urllib.request, "urlopen",
                               side_effect=lambda req, timeout=None: FakeResp(b"x" * 999)), \
                mock.patch.object(mr.urllib.request, "build_opener",
                                  return_value=DirectOpener()):
            with self.assertRaises(ValueError):
                mr._fetch(url, cap=10)
            self.assertEqual(calls, [])   # 没走到直连重试


class TestMarketRemoteGhFiles(BaseTest):
    """git 类来源下载通道：github URL 解析、codeload tarball 主通道（安全解包/
    前缀/穿越拒绝）、jsdelivr 兜底的前缀过滤、全失败报错。"""

    def runTest(self):
        from app.core import market_remote as mr

        # github URL 解析
        self.assertEqual(mr._gh_split("https://github.com/o/r.git"), ("o", "r"))
        self.assertEqual(mr._gh_split("https://github.com/o/r/"), ("o", "r"))
        self.assertEqual(mr._gh_split("https://github.com/o/r"), ("o", "r"))
        self.assertIsNone(mr._gh_split("https://gitlab.com/o/r.git"))
        self.assertIsNone(mr._gh_split("https://github.com/evil/../r.git"))

        def make_tar(files, top="repo-main"):
            """{相对路径: bytes} → codeload 形态的 tar.gz bytes（带顶层目录）。"""
            import io as _io
            import tarfile as _tf
            buf = _io.BytesIO()
            with _tf.open(fileobj=buf, mode="w:gz") as tf:
                for rel, data in files.items():
                    info = _tf.TarInfo(top + "/" + rel)
                    info.size = len(data)
                    tf.addfile(info, _io.BytesIO(data))
            return buf.getvalue()

        good_tar = make_tar({
            "plugins/docs/SKILL.md": SKILL_MD.encode("utf-8"),
            "plugins/docs/ref.md": REF_MD.encode("utf-8"),
            "plugins/other/SKILL.md": b"x",      # 前缀外，不进插件根
        })
        entry = {"id": "remote-t-gh", "title": "GH", "desc": "d",
                 "source_id": "t", "source_name": "T", "version": "", "homepage": "",
                 "install": {"kind": "git", "url": "https://github.com/o/r.git",
                             "path": "plugins/docs", "ref": "", "skills": []}}

        # tarball 主通道：根=解包后前缀目录，前缀外文件不出现
        fetcher, resolver = _patch_fetch(mr, lambda url: good_tar)
        with fetcher, resolver:
            tmp = Path(tempfile.mkdtemp())
            try:
                root = mr._gh_tarball(entry, tmp)
                self.assertTrue((root / "SKILL.md").is_file())
                self.assertTrue((root / "ref.md").is_file())
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        # 穿越/链接成员拒绝
        import io as _io
        import tarfile as _tf
        evil = _io.BytesIO()
        with _tf.open(fileobj=evil, mode="w:gz") as tf:
            info = _tf.TarInfo("repo-main/../evil.txt")
            info.size = 1
            tf.addfile(info, _io.BytesIO(b"x"))
        fetcher, resolver = _patch_fetch(mr, lambda url: evil.getvalue())
        with fetcher, resolver:
            tmp = Path(tempfile.mkdtemp())
            try:
                with self.assertRaises(ValueError):
                    mr._gh_tarball(entry, tmp)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        # 端到端：tarball 通道装的包装进 skillpack
        cache = self.data_dir / "market_remote"
        cache.mkdir(parents=True)
        (cache / "t.json").write_text(json.dumps(
            {"fetched_at": "", "url": "", "catalog": {"plugins": [
                {"name": "docs", "description": "d",
                 "source": {"source": "repo-relative", "path": "plugins/docs"}}]}},
            ensure_ascii=False), encoding="utf-8")
        src_patch = mock.patch.object(
            mr, "SOURCES",
            [{"id": "t", "name": "T", "kind": "marketplace",
              "repo": "https://github.com/o/r.git", "urls": ["https://x/a.json"]}])
        fetcher, resolver = _patch_fetch(mr, lambda url: good_tar)
        with fetcher, resolver, src_patch:
            res, err = mr.install_remote("remote-t-docs")
        self.assertIsNone(err, err)
        f = self.data_dir / "skillpacks" / "market-remote-t-docs.md"
        self.assertTrue(f.is_file())
        self.assertIn("market_id: remote-t-docs", f.read_text(encoding="utf-8"))

        # 全通道失败 → 合并报错（codeload + jsdelivr 两段原因都在）
        def dead(url):
            raise urllib.error.URLError("net down")
        fetcher, resolver = _patch_fetch(mr, dead)
        with fetcher, resolver, src_patch:
            res, err = mr.install_remote("remote-t-docs")
        self.assertIsNone(res)
        self.assertIn("codeload", err)
        self.assertIn("jsdelivr", err)

        # jsdelivr 兜底通道仍然可用（codeload 404 时）：目录树→逐文件、前缀过滤
        tree = {"files": [
            {"name": "/plugins/docs/SKILL.md", "size": 200},
            {"name": "/plugins/docs/ref.md", "size": 50},
            {"name": "/plugins/other/SKILL.md", "size": 200},
        ]}
        cdn_calls = []

        def jd_responder(url):
            if "codeload.github.com" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            if "data.jsdelivr.com" in url:
                return json.dumps(tree).encode("utf-8")
            cdn_calls.append(url)
            return (SKILL_MD if url.endswith("SKILL.md") else REF_MD).encode("utf-8")

        fetcher, resolver = _patch_fetch(mr, jd_responder)
        with fetcher, resolver:
            tmp = Path(tempfile.mkdtemp())
            try:
                root = mr._gh_fetch_files(entry, tmp)
                self.assertTrue((root / "SKILL.md").is_file())
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(len(cdn_calls), 2, cdn_calls)   # 前缀过滤：只有 2 个文件


class TestMarketRemotePaging(BaseTest):
    """view() 服务端分页与过滤：切页 has_more、搜索、来源筛选、组合过滤。"""

    def runTest(self):
        from app.core import market_remote as mr
        cache = self.data_dir / "market_remote"
        cache.mkdir(parents=True)
        plugins = [{"name": "alpha-%d" % i, "description": "desc %d" % i,
                    "source": {"source": "clawhub", "slug": "alpha-%d" % i}}
                   for i in range(5)]
        plugins.append({"name": "beta", "description": "searchable needle",
                        "source": {"source": "clawhub", "slug": "beta"}})
        (cache / "zcode.json").write_text(json.dumps(
            {"fetched_at": "t", "url": "", "catalog": {"plugins": plugins}},
            ensure_ascii=False), encoding="utf-8")

        # 全量一页装不下：切页与 has_more
        p1 = mr.view(offset=0, limit=2)
        self.assertEqual(len(p1["entries"]), 2)
        self.assertEqual(p1["total"], 6)
        self.assertTrue(p1["has_more"])
        p3 = mr.view(offset=4, limit=2)
        self.assertEqual(len(p3["entries"]), 2)
        self.assertFalse(p3["has_more"])
        p4 = mr.view(offset=6, limit=2)
        self.assertEqual(p4["entries"], [])
        # 页与页不重叠
        ids1 = {e["id"] for e in p1["entries"]}
        ids3 = {e["id"] for e in p3["entries"]}
        self.assertFalse(ids1 & ids3)

        # 搜索（大小写不敏感，命中 name/desc）
        hit = mr.view(q="NEEDLE")
        self.assertEqual(hit["total"], 1)
        self.assertEqual(hit["entries"][0]["name"], "beta")
        # 来源筛选
        only = mr.view(source="zcode")
        self.assertEqual(only["total"], 6)
        none = mr.view(source="anthropic")
        self.assertEqual(none["total"], 0)
        self.assertFalse(none["has_more"])
        # 组合：搜索 + 分页
        combo = mr.view(q="alpha-", limit=2)
        self.assertEqual(combo["total"], 5)
        self.assertEqual(len(combo["entries"]), 2)
        self.assertTrue(combo["has_more"])

        # 上限夹紧：limit 超界按 500 封顶、offset 负数归 0
        big = mr.view(offset=-5, limit=9999)
        self.assertEqual(len(big["entries"]), 6)
        self.assertEqual(big["offset"], 0)


class TestMarketRemoteClawhub(BaseTest):
    def runTest(self):
        from app.core import market_remote as mr
        page1 = {"items": [
            {"slug": "alpha", "displayName": "Alpha", "summary": "s-a",
             "tags": {"latest": "1.0.1"}, "topics": ["git"],
             "stats": {"downloads": 5}},
            {"slug": "beta", "displayName": "Beta", "summary": "s-b",
             "topics": ["mcp"], "stats": {"downloads": 2}},
        ], "nextCursor": "c1"}
        page2 = {"items": [
            {"slug": "alpha", "displayName": "Alpha", "summary": "s-a"},   # 重复 slug 应去重
            {"slug": "gamma", "displayName": "Gamma", "summary": "s-g"},
        ], "nextCursor": None}
        trending = {"items": [
            {"displayName": "Delta", "summary": "s-d",
             "install": {"reference": "own/delta"}},
        ]}
        payloads = [json.dumps(page1).encode(), json.dumps(page2).encode(),
                    json.dumps(trending).encode()]
        calls = []

        def responder(url):
            calls.append(url)
            return payloads[min(len(calls) - 1, len(payloads) - 1)]

        fetcher, resolver = _patch_fetch(mr, responder)
        with fetcher, resolver:
            v = mr.refresh(source_id="clawhub")
        self.assertTrue(v["refresh"][0]["ok"])
        self.assertEqual(v["refresh"][0]["count"], 4)   # alpha 去重后 3 + trending 1
        by = {e["name"]: e for e in v["entries"]}
        self.assertEqual(by["alpha"]["compat"], "ok")
        self.assertEqual(by["beta"]["compat"], "ok")          # topics 带 mcp → 剥离式可装
        self.assertEqual(by["delta"]["install"]["kind"], "clawhub")
        self.assertEqual(by["delta"]["install"]["reference"], "own/delta")
        self.assertIn("trending", by["delta"]["keywords"])
        self.assertEqual(by["alpha"]["version"], "1.0.1")
