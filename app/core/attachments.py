# -*- coding: utf-8 -*-
"""任务附件（截图/文件）：先落「待提交区」，创建任务时移入工作目录 _attachments/。

流程：UI 选好/粘贴文件 → POST /api/attachments（base64）→ save_pending 落
data/pending/<id>；创建任务时 payload.attachments 带 id 列表 →
commit_to_workdir 移入 <workdir>/_attachments/ 并把路径清单注入任务上下文
（context），所有 __CONTEXT__ 注入点（规划/实现/评审/起草）自然可见。
图片附件另走 run_agent(images=...)（codex --image），供起草/实现智能体直接"看图"。

安全：文件名去路径化并挡 ..；扩展名白名单；单文件/总数量上限；
待提交区有兜底清理（启动时 + 每次提交时顺手），防止崩溃残留堆积。
"""
from __future__ import annotations

import base64
import html as _html
import json
import mimetypes
import re
import secrets
import time
import zipfile
from pathlib import Path

from . import paths

MAX_FILES = 12
MAX_BYTES = 8 * 1024 * 1024  # 单文件 8MB：截图/文本足够，挡恶意大包
# Office 文档压缩包实际可到几十 MB（带图的 Excel 很常见），放宽到 24MB
OFFICE_MAX_BYTES = 24 * 1024 * 1024
_ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
    ".txt", ".md", ".markdown", ".csv", ".json", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".xml", ".yaml", ".yml", ".toml",
    ".pdf",
    # Office 文档：现代 zip 格式（docx/xlsx/pptx）会另抽文本伴生文件；
    # 老式二进制（doc/xls/ppt）与 rtf/odt/ods 只落盘，由能读文件的智能体自行处理
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".rtf", ".odt", ".ods",
}
# 可零依赖抽文本的 zip 系 Office 格式（zipfile + ElementTree，无第三方库）
_TEXT_EXTRACT_EXT = {".docx", ".xlsx", ".pptx"}
_EXTRACT_MAX_CHARS = 200000  # 伴生文本上限，防巨型文档灌爆上下文
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
# 控制字符/Windows 非法字符/路径分隔一律清掉；中文名保留（落盘和 CLI 都吃得下）
_NAME_BAD = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def _pending_dir() -> Path:
    d = paths.DATA_DIR / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(raw):
    name = str(raw or "file").split("/")[-1].split("\\")[-1].strip()
    name = _NAME_BAD.sub("_", name).strip(". ")
    if not name:
        name = "file"
    return name[:120]


def save_pending(name, data_b64):
    """校验并保存一个待提交附件。返回 {id,name,size,mime}；失败抛 ValueError。"""
    name = _safe_name(name)
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError("不支持的附件类型 %s（支持图片/文本/Word/Excel/PPT/PDF）"
                         % (ext or "无扩展名"))
    try:
        data = base64.b64decode(str(data_b64 or ""), validate=True)
    except Exception:
        raise ValueError("附件数据损坏（base64 解码失败）")
    if not data:
        raise ValueError("附件内容为空: %s" % name)
    cap = OFFICE_MAX_BYTES if ext in _TEXT_EXTRACT_EXT or ext in (
        ".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods") else MAX_BYTES
    if len(data) > cap:
        raise ValueError("附件超过 %dMB: %s" % (cap // 1024 // 1024, name))
    fid = secrets.token_hex(8)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    (_pending_dir() / fid).write_bytes(data)
    meta = {"id": fid, "name": name, "size": len(data), "mime": mime}
    (_pending_dir() / (fid + ".json")).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def norm_rel(a):
    """把一条附件记录归一成工作目录内相对路径（正斜杠）。

    附件只会落在 <workdir>/_attachments/ 下；老数据可能是绝对路径或裸文件名，
    统一钉回 _attachments/ 起头的相对位置，供前端「点击查看」直接走
    /api/runs/<id>/file?name= 通道。取不到路径返回空串。"""
    if isinstance(a, dict):
        p = str(a.get("path") or a.get("name") or "")
    else:
        p = str(a or "")
    p = p.replace("\\", "/").strip()
    if not p:
        return ""
    if p.startswith("_attachments/"):
        return p
    return "_attachments/" + p.rsplit("/", 1)[-1]


def read_pending(fid):
    """读待提交区附件原文（输入条胶囊点击预览用）。id 是 16 位十六进制
    随机数且只认文件名，猜不中即空。返回 (bytes, mime, name)；找不到
    返回 (b"", "", "")。"""
    fid = str(fid or "")
    if not _ID_RE.match(fid):
        return b"", "", ""
    p = _pending_dir() / fid
    if not p.is_file():
        return b"", "", ""
    meta = {}
    try:
        meta = json.loads((_pending_dir() / (fid + ".json")).read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        data = p.read_bytes()
    except OSError:
        return b"", "", ""
    return data, str(meta.get("mime") or "application/octet-stream"), _safe_name(meta.get("name") or fid)


_XML_PART_MAX = 20 * 1024 * 1024  # 单个 Office 部件（XML 条目）读入上限


def _safe_xml(data):
    """解析不可信 XML 前的防护：拒 DTD/实体声明（防实体扩展炸弹）+ 限尺寸。
    正常 Office 部件不含 DOCTYPE；触发即视为恶意包，由上层放弃抽取。"""
    if len(data) > _XML_PART_MAX:
        raise ValueError("xml part too large")
    head = data[:4096].lstrip()
    if head.startswith(b"<?xml"):
        head = head.split(b"?>", 1)[-1].lstrip()
    if head[:9].lower() == b"<!doctype" or b"<!ENTITY" in data[:65536].upper():
        raise ValueError("dtd/entity not allowed")
    import xml.etree.ElementTree as ET
    return ET.fromstring(data)


def _zip_read(zf, name):
    """按尺寸上限读 zip 条目；超限抛错（上层吞掉放弃抽取）。"""
    info = zf.getinfo(name)
    if info.file_size > _XML_PART_MAX:
        raise ValueError("zip entry too large: %s" % name)
    return zf.read(name)


def _xml_text(xml_bytes):
    """粗提 XML 里的可见文本：非文本标签当段落边界，仅保留 w:t/t/a:t 内容。
    正则剥离本身不展开实体，但仍拒含 DTD/实体的部件（恶意包特征）：返回 None。"""
    head = xml_bytes[:4096].lstrip()
    if head.startswith(b"<?xml"):
        head = head.split(b"?>", 1)[-1].lstrip()
    if head[:9].lower() == b"<!doctype" or b"<!ENTITY" in xml_bytes[:65536].upper():
        raise ValueError("dtd/entity not allowed")
    txt = xml_bytes.decode("utf-8", "replace")
    txt = re.sub(r"<(?!/?(?:w:t|t|a:t)\b)[^>]*>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    return _html.unescape(txt)


def _xlsx_text(zf):
    """xlsx → 工作表文本：共享字符串 + 各 sheet 单元格按行列还原（够 agent 看内容）。"""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    mtag = "{%s}" % ns["m"]
    shared = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = _safe_xml(_zip_read(zf, "xl/sharedStrings.xml"))
        for si in root.findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(mtag + "t")))
    out = []
    wb = _safe_xml(_zip_read(zf, "xl/workbook.xml"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in zf.namelist():
        for rel in _safe_xml(_zip_read(zf, "xl/_rels/workbook.xml.rels")):
            rels[rel.get("Id")] = rel.get("Target") or ""
    for sh in wb.find("m:sheets", ns) or []:
        name = sh.get("name") or "Sheet"
        rid = sh.get("{%s}id" % ns["r"]) or ""
        target = rels.get(rid, "")
        if target and not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        if target not in zf.namelist():
            continue
        lines = ["### 工作表: " + name]
        try:
            ws = _safe_xml(_zip_read(zf, target))
            for row in ws.iter(mtag + "row"):
                cells = []
                for c in row.findall("m:c", ns):
                    t = c.get("t")
                    if t == "s":
                        v = c.find("m:v", ns)
                        cells.append(shared[int(v.text)] if v is not None
                                     and v.text and int(v.text) < len(shared) else "")
                    elif t == "inlineStr":
                        is_el = c.find("m:is", ns)
                        cells.append("".join(x.text or "" for x in is_el.iter(mtag + "t"))
                                     if is_el is not None else "")
                    else:
                        v = c.find("m:v", ns)
                        cells.append((v.text or "") if v is not None else "")
                if any(cells):
                    lines.append("\t".join(cells))
        except Exception:
            lines.append("（该工作表解析失败）")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def extract_office_text(path):
    """docx/xlsx/pptx → 纯文本（零依赖 zipfile+ElementTree，带 DTD/实体防护）。
    失败/无文本返回 None——绝不因抽取失败拒收附件。"""
    try:
        with zipfile.ZipFile(str(path)) as zf:
            names = zf.namelist()
            if "word/document.xml" in names:
                txt = _xml_text(_zip_read(zf, "word/document.xml"))
            elif "xl/workbook.xml" in names:
                txt = _xlsx_text(zf)
            else:
                slides = sorted((n for n in names
                                 if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
                                key=lambda n: int(re.search(r"(\d+)", n).group(1)))[:100]
                if not slides:
                    return None
                txt = "\n\n".join(
                    "### 幻灯片 %d\n%s" % (i + 1, _xml_text(_zip_read(zf, n)).strip())
                    for i, n in enumerate(slides))
        txt = re.sub(r"\n{3,}", "\n\n", txt or "").strip()
        if not txt:
            return None
        if len(txt) > _EXTRACT_MAX_CHARS:
            txt = txt[:_EXTRACT_MAX_CHARS] + "\n…（超长截断）"
        return txt
    except Exception:
        return None


def commit_to_workdir(workdir, ids):
    """把待提交附件移入 <workdir>/_attachments/，返回 [{name,size,mime,path}]。

    path 为工作目录内相对路径（正斜杠）。非法/已丢失的 id 静默跳过
    （上传与创建之间隔着用户编辑，包不能因个别文件过期而整体失败；
    跳过的数量由调用方对照 UI 列表可见）。
    """
    out = []
    pend = _pending_dir()
    adir = Path(workdir) / "_attachments"
    for fid in (ids or [])[:MAX_FILES]:
        fid = str(fid)
        if not _ID_RE.match(fid):
            continue
        f = pend / fid
        if not f.is_file():
            continue
        meta = {}
        try:
            meta = json.loads((pend / (fid + ".json")).read_text(encoding="utf-8"))
        except Exception:
            pass
        name = _safe_name(meta.get("name") or fid)
        stem, ext = Path(name).stem, Path(name).suffix
        i = 2
        while (adir / name).exists():
            name = "%s-%d%s" % (stem, i, ext)
            i += 1
        try:
            adir.mkdir(parents=True, exist_ok=True)
            f.replace(adir / name)  # 同盘 move；跨盘回落 copy+delete
            if not (adir / name).is_file():
                (adir / name).write_bytes(f.read_bytes())
                f.unlink(missing_ok=True)
        except OSError:
            continue
        finally:
            (pend / (fid + ".json")).unlink(missing_ok=True)
        item = {"name": name,
                "size": meta.get("size") or (adir / name).stat().st_size,
                "mime": meta.get("mime") or "application/octet-stream",
                "path": "_attachments/" + name}
        # Office zip 文档抽正文 → 伴生 .txt：无头 CLI 读不了二进制容器，
        # 有文本版所有智能体都能直接读；抽取失败只降级不拒收
        if Path(name).suffix.lower() in _TEXT_EXTRACT_EXT:
            try:
                body = extract_office_text(adir / name)
            except Exception:
                body = None
            if body:
                side = name + ".txt"
                try:
                    (adir / side).write_text(body, encoding="utf-8")
                    item["text_path"] = "_attachments/" + side
                except OSError:
                    pass
        out.append(item)
    return out


def context_block(items):
    """附件清单文本，追加到任务 context。相对 workdir，重试/续跑同目录仍有效。"""
    if not items:
        return ""
    lines = ["", "## 附件材料（位于工作目录 _attachments/，可直接读取）"]
    for a in items:
        kind = "图片" if str(a.get("mime", "")).startswith("image/") else "文件"
        line = "- %s（%s，%s）" % (a["path"], kind, _human(a.get("size") or 0))
        if a.get("text_path"):
            line += "，正文文本版见 %s（优先读它）" % a["text_path"]
        lines.append(line)
    lines.append("请在处理目标时参考以上附件；图片附件可直接查看内容。")
    return "\n".join(lines)


def image_paths(task, workdir, limit=6):
    """任务图片附件的绝对路径（传给 codex --image）。缺失的跳过。"""
    out = []
    for a in (task.get("attachments") or []):
        if not str(a.get("mime", "")).startswith("image/"):
            continue
        p = Path(workdir) / str(a.get("path") or "")
        try:
            if a.get("path") and p.is_file():
                out.append(str(p))
        except OSError:
            continue
        if len(out) >= limit:
            break
    return out


def cleanup_stale(max_age_s=7 * 86400):
    """清理超过 max_age_s 的待提交残留（启动时调用）。返回删除数。"""
    n = 0
    try:
        pend = _pending_dir()
    except OSError:
        return 0
    now = time.time()
    for f in pend.glob("*"):
        try:
            if now - f.stat().st_mtime > max_age_s:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


def _human(n):
    if n >= 1024 * 1024:
        return "%.1fMB" % (n / 1024 / 1024)
    if n >= 1024:
        return "%dKB" % (n // 1024)
    return "%dB" % n
