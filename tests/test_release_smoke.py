# -*- coding: utf-8 -*-
"""release_smoke：npm 发版前打包内容校验（WorkDSH 发布工程借鉴 + 0.1.63 坏包教训）。

0.1.63 事故：步骤一错即 NameError 崩 run——坏文件进了 npm 包才发现。
HEAD worktree 发布法解决「未提交在制品混入」，本工具解决「已提交内容
本身是坏的」：对将要发布的包做三道校验——

1. SHA256SUMS 清单：包内全部文件哈希（从 tar 流直读）落盘，发布产物
   可追溯可复核
2. import 冒烟：安全解包后逐个 import app/core 模块，任何
   ImportError/SyntaxError 当场红（不再等用户跑起来才崩）
3. bin 入口在包内（薄壳 bin/tutti.js 缺失=装完没有命令）

两种用法：
- 单测（防 tar-slip/清单口径，进常规套跑）：
    python -m unittest discover -s tests -p "test_release_smoke.py"
- 发版全量校验（test_selfupdate 之后、npm publish 之前，退出码非 0 不发版）：
    python tests/test_release_smoke.py --pack
    python tests/test_release_smoke.py codebee-0.1.67.tgz

解包安全：与 app/core/market_remote._safe_extract 同款三重防线——
段字符白名单、resolve 收容校验、落点复查（3.8 无 extractall
filter=，逐成员手动写出）。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEG_OK = re.compile(r"^\.{0,2}[A-Za-z0-9_][A-Za-z0-9_. ()\[\]-]*$")


# ---------- 工具函数（单测与 CLI 共用） ----------

def _rel_parts(name):
    return [p for p in name.replace("\\", "/").split("/") if p]


def _check_member(name):
    """tar 成员名三重防线之一：段级校验，危险即抛 ValueError。"""
    parts = _rel_parts(name)
    if not parts:
        raise ValueError("tar 内路径可疑: %r" % name)
    first = name.replace("\\", "/").split("/")[0]
    if name.startswith(("/", "\\")) or ":" in first:
        raise ValueError("tar 内路径可疑: %r" % name)
    if any(seg in ("..", ".") or not _SEG_OK.match(seg) for seg in parts):
        raise ValueError("tar 内路径可疑: %r" % name)
    return parts


def tar_manifest(tf, out_path):
    """从 tar 流直读写 SHA256SUMS（成员名即相对路径，不经文件系统）。"""
    rows = []
    for m in tf.getmembers():
        if m.isdir() or not m.isfile():
            continue
        parts = _check_member(m.name)
        rel = "/".join(parts)
        src = tf.extractfile(m)
        if src is None:
            continue
        rows.append((rel, hashlib.sha256(src.read()).hexdigest()))
    rows.sort()
    lines = "".join("%s  %s\n" % (digest, rel) for rel, digest in rows)
    Path(out_path).write_bytes(lines.encode("utf-8"))
    return len(rows)


def safe_extract_tar(tf, dest):
    """防炸弹解包：逐成员手动写出（不用 extractall），三重防线同
    market_remote._safe_extract——段白名单、resolve 收容、落点复查。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    base = dest.resolve()
    written = 0
    for m in tf.getmembers():
        if not m.isfile():
            if m.issym() or m.islnk():
                raise ValueError("包不应含链接成员: %s" % m.name)
            continue
        parts = _check_member(m.name)
        p = Path(os.path.join(str(dest), *parts)).resolve()
        if base != p and base not in p.parents:
            raise ValueError("tar 内路径可疑: %r" % m.name)
        src = tf.extractfile(m)
        if src is None:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(src.read())
        written += 1
    for p in dest.rglob("*"):
        rp = p.resolve()
        if base != rp and base not in p.parents:
            raise ValueError("解包落点越界: %s" % p)
    return written


def smoke_imports(root, mods=None):
    """在 root 上逐个 import 模块（独立子进程，一个崩不连坐其余）。

    mods 缺省=全部 app/core/*.py（不含 main——它起服务）；
    传入子集供单测快速路径。返回 (模块列表, 失败清单 [(mod, err_tail)])。"""
    if mods is None:
        core_dir = os.path.join(root, "app", "core")
        mods = ["app.core." + n[:-3] for n in sorted(os.listdir(core_dir))
                if n.endswith(".py") and not n.startswith("_")
                and n != "main.py"]
        if os.path.isfile(os.path.join(root, "app", "__init__.py")):
            mods.insert(0, "app")
    failures = []
    for m in mods:
        r = subprocess.run([sys.executable, "-c", "import %s" % m],
                           capture_output=True, text=True, cwd=root, timeout=120)
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()
            failures.append((m, tail[-1] if tail else "exit %d" % r.returncode))
    return mods, failures


def check_bin(root):
    return os.path.isfile(os.path.join(root, "bin", "tutti.js"))


# ---------- 单测 ----------

class MemberCheckTests(unittest.TestCase):

    def test_rejects_traversal(self):
        for bad in ("../x.py", "a/../../x.py", "/abs/x.py", "C:/x.py",
                    "..\\x.py", "", "./x.py"):
            with self.assertRaises(ValueError):
                _check_member(bad)

    def test_accepts_normal(self):
        for ok in ("package/app/core/paihang.py", "package/bin/tutti.js",
                   "package/README.md", "package/app-ui (v2).md"):
            self.assertTrue(_check_member(ok))


class SafeExtractTests(unittest.TestCase):

    def _tar_bytes(self, name, content=b"x", sym=False):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            ti = tarfile.TarInfo(name)
            ti.size = len(content)
            if sym:
                ti.type = tarfile.SYMTYPE
                ti.linkname = "../outside.txt"
                tf.addfile(ti)
            else:
                tf.addfile(ti, io.BytesIO(content))
        return buf.getvalue()

    def test_extract_clean_and_reject_traversal(self):
        tmp = tempfile.mkdtemp(prefix="rsmoke_")
        # 干净成员：解出一个文件且内容正确
        with tarfile.open(fileobj=io.BytesIO(self._tar_bytes("pkg/a.py", b"ok"))) as tf:
            n = safe_extract_tar(tf, os.path.join(tmp, "clean"))
        self.assertEqual(n, 1)
        got = Path(tmp) / "clean" / "pkg" / "a.py"
        self.assertEqual(got.read_bytes(), b"ok")
        # 穿越/链接成员：拒绝
        for name, sym in (("pkg/../evil.py", False), ("pkg/link", True)):
            with tarfile.open(fileobj=io.BytesIO(self._tar_bytes(name, sym=sym))) as tf:
                with self.assertRaises(ValueError):
                    safe_extract_tar(tf, os.path.join(tmp, "bad"))


class ManifestTests(unittest.TestCase):

    def test_manifest_from_tar_stream(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name, data in (("pkg/b.txt", b"bbb"), ("pkg/a.txt", b"aaa")):
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
        tmp = tempfile.mkdtemp(prefix="rsmoke_")
        out = os.path.join(tmp, "SUMS")
        with tarfile.open(fileobj=io.BytesIO(buf.getvalue())) as tf:
            n = tar_manifest(tf, out)
        self.assertEqual(n, 2)
        text = Path(out).read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(lines[0].split("  ")[1], "pkg/a.txt")   # 排序
        self.assertTrue(lines[1].startswith(hashlib.sha256(b"bbb").hexdigest()))
        self.assertTrue(text.endswith("\n") and "\r" not in text)   # LF 钉死


class SmokeImportTests(unittest.TestCase):

    def test_fast_modules_import(self):
        # 子集快速路径：只冒烟两个轻模块（全量 65+ 模块走 CLI 发版档）
        mods, failures = smoke_imports(REPO, ["app.core.paihang",
                                              "app.core.defectretro"])
        self.assertEqual(failures, [])
        self.assertEqual(len(mods), 2)

    def test_failure_reported(self):
        _mods, failures = smoke_imports(REPO, ["app.core.no_such_module_xyz"])
        self.assertEqual(len(failures), 1)
        self.assertIn("no_such_module_xyz", failures[0][0])


# ---------- CLI（发版档） ----------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    pack = "--pack" in argv
    tarball = next((a for a in argv if not a.startswith("-")), None)

    if pack or not tarball:
        print("[1/4] npm pack ...")
        r = subprocess.run(["npm", "pack", "--json"], capture_output=True,
                           text=True, shell=(os.name == "nt"), cwd=REPO)
        if r.returncode != 0:
            print("npm pack 失败：\n%s" % ((r.stderr or r.stdout)[-2000:]))
            return 2
        import json
        tarball = os.path.join(REPO, json.loads(r.stdout)[0]["filename"])
        print("    打出 %s" % tarball)

    if not os.path.isfile(tarball):
        print("tarball 不存在: %s" % tarball)
        return 2

    work = tempfile.mkdtemp(prefix="relsmoke_")
    print("[2/4] SHA256SUMS（tar 流直读）+ 安全解包 ...")
    with tarfile.open(tarball) as tf:
        n = tar_manifest(tf, os.path.join(work, "SHA256SUMS"))
        root = os.path.join(work, "src")
        written = safe_extract_tar(tf, root)
    # npm 包成员一律带 package/ 前缀
    if os.path.isdir(os.path.join(root, "package")):
        root = os.path.join(root, "package")
    if not os.path.isdir(os.path.join(root, "app", "core")):
        print("    FAIL 解包后找不到 app/core（包布局异常）")
        return 1
    print("    清单 %d 文件、解包 %d 文件；SUMS 在 %s"
          % (n, written, os.path.join(work, "SHA256SUMS")))

    print("[3/4] import 冒烟 ...")
    mods, failures = smoke_imports(root)
    print("    import %d 个模块，失败 %d" % (len(mods), len(failures)))
    for m, err in failures:
        print("    FAIL %s: %s" % (m, err[:200]))

    print("[4/4] bin 入口 ...")
    if not check_bin(root):
        failures.append(("bin/tutti.js", "缺失"))
        print("    FAIL bin/tutti.js 缺失")

    if failures:
        print("\n结果：不过（%d 处）——不发版" % len(failures))
        return 1
    print("\n结果：过（%d 文件 / %d 模块 / bin 在）——可 publish" % (n, len(mods)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
