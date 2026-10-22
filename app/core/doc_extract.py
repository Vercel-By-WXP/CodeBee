# -*- coding: utf-8 -*-
"""Local-first text extraction for PDF and legacy office documents.

Modern OOXML containers are handled by ``attachments.extract_office_text``
with zipfile + ElementTree.  This module covers the formats that need a real
engine: PDF (pypdf / PyMuPDF / pdftotext / mutool) and legacy Word/Excel/PPT,
RTF, ODT and ODS (LibreOffice headless).  RTF additionally has a small
standard-library fallback so plain RTF always yields text.

Engines are *discovered*, never downloaded or bundled.  Missing engines are a
capability gap, not an upload error: ``extract_text`` returns ``None`` and the
attachment is still committed.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_CHARS = 200000
MAX_PAGES = 400
MAX_ENGINE_BYTES = 24 * 1024 * 1024
PDF_TIMEOUT = 60
SOFFICE_TIMEOUT = 120
SUPPORTED_EXT = {".pdf", ".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods"}
PDF_ENGINES = ("pypdf", "pymupdf", "pdftotext", "mutool")

_RTF_SKIP = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header",
    "footer", "footnote", "annotation", "fldinst", "xmlnstbl", "listtable",
    "listoverridetable", "rsidtbl", "generator", "themedata", "colorschememapping",
    "datastore", "latentstyles", "listpicture",
}


def _module_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _soffice_path():
    for name in ("soffice", "soffice.exe", "libreoffice", "libreoffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    roots = [os.environ.get("PROGRAMFILES") or "",
             os.environ.get("PROGRAMFILES(X86)") or "",
             os.environ.get("PROGRAMW6432") or ""]
    candidates = [Path(root) / "LibreOffice" / "program" / "soffice.exe"
                  for root in roots if root]
    candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def available():
    """Describe the engines visible to this process without running them."""
    engines = [
        {"id": "pypdf", "kind": "python", "formats": ["pdf"],
         "available": _module_available("pypdf")},
        {"id": "pymupdf", "kind": "python", "formats": ["pdf"],
         "available": _module_available("fitz")},
    ]
    for name, exe in (("pdftotext", "pdftotext"), ("mutool", "mutool")):
        path = shutil.which(exe) or ""
        engines.append({"id": name, "kind": "cli", "formats": ["pdf"],
                        "available": bool(path), "path": path})
    soffice = _soffice_path()
    engines.append({"id": "libreoffice", "kind": "cli",
                    "formats": ["doc", "xls", "ppt", "rtf", "odt", "ods"],
                    "available": bool(soffice), "path": soffice})
    engines.append({"id": "rtf-parser", "kind": "builtin", "formats": ["rtf"],
                    "available": True})
    return {"engines": engines, "soffice": soffice}


def _run(cmd, timeout):
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
              "timeout": timeout, "check": False}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    if flags:
        kwargs["creationflags"] = flags
    return subprocess.run(cmd, **kwargs)


def _read_text(path):
    try:
        raw = Path(path).read_bytes()[:MAX_ENGINE_BYTES]
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _normalize(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cap(text, max_chars):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…（文档超长，已截断）"


def _pdf_with_pypdf(path):
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path), strict=False)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                return ""
        chunks = []
        for index, page in enumerate(reader.pages):
            if index >= MAX_PAGES:
                break
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(chunks)
    except Exception:
        return ""


def _pdf_with_pymupdf(path):
    try:
        import fitz
    except Exception:
        return ""
    try:
        doc = fitz.open(str(path))
    except Exception:
        return ""
    chunks = []
    try:
        for index in range(min(len(doc), MAX_PAGES)):
            try:
                chunks.append(doc.load_page(index).get_text("text"))
            except Exception:
                continue
    finally:
        doc.close()
    return "\n".join(chunks)


def _cli_text(exe, args):
    if not exe:
        return ""
    try:
        proc = _run([exe] + args, PDF_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0 or not proc.stdout:
        return ""
    return proc.stdout.decode("utf-8", "replace")


def _rtf_to_text(raw):
    """Minimal RTF reader: groups, destinations, escapes, ``\\u`` and ``\\'hh``."""
    text = raw.decode("latin-1", "replace")
    out = []
    buf = bytearray()
    stack = []
    skip = False
    uc = 1
    unicode_skip = 0

    def flush():
        if buf:
            segment = bytes(buf)
            del buf[:]
            try:
                out.append(segment.decode("gb18030"))
            except UnicodeDecodeError:
                out.append(segment.decode("cp1252", "replace"))

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            stack.append(skip)
            i += 1
            continue
        if ch == "}":
            flush()
            if stack:
                skip = stack.pop()
            i += 1
            continue
        if ch == "\\":
            j = i + 1
            if j >= n:
                break
            nxt = text[j]
            if nxt in "\\{}":
                if not skip:
                    flush()
                    out.append(nxt)
                i = j + 1
                unicode_skip = 0
                continue
            if nxt == "'":
                try:
                    buf.append(int(text[j + 1:j + 3], 16))
                except ValueError:
                    pass
                i = j + 3
                unicode_skip = 0
                continue
            if nxt == "*":
                skip = True
                i = j + 1
                continue
            k = j
            while k < n and text[k].isalpha():
                k += 1
            word = text[j:k]
            m = k
            if m < n and text[m] == "-":
                m += 1
            while m < n and text[m].isdigit():
                m += 1
            param = text[k:m]
            if m < n and text[m] == " ":
                m += 1
            if word == "uc":
                try:
                    uc = max(0, int(param or 1))
                except ValueError:
                    uc = 1
            elif word == "u":
                try:
                    code = int(param)
                except ValueError:
                    code = 0
                if code < 0:
                    code += 65536
                if not skip:
                    flush()
                    out.append(chr(code))
                unicode_skip = uc
            elif word in ("par", "line", "row"):
                if not skip:
                    flush()
                    out.append("\n")
            elif word in ("tab", "cell"):
                if not skip:
                    flush()
                    out.append("\t")
            elif word in _RTF_SKIP:
                skip = True
            i = m
            continue
        if unicode_skip:
            unicode_skip -= 1
            i += 1
            continue
        if ch in "\r\n":
            i += 1
            continue
        if not skip:
            if ord(ch) < 128:
                buf.append(ord(ch))
            else:
                flush()
                out.append(ch)
        i += 1
    flush()
    return "".join(out)


def _soffice_convert(path):
    exe = _soffice_path()
    if not exe:
        return ""
    with tempfile.TemporaryDirectory(prefix="codebee-doc-") as tmp:
        outdir = Path(tmp)
        profile = (outdir / "profile").as_uri()
        cmd = [exe, "--headless", "--nologo", "--nodefault", "--norestore",
               "-env:UserInstallation=" + profile,
               "--convert-to", "txt:Text (encoded):UTF8",
               "--outdir", str(outdir), str(path)]
        try:
            _run(cmd, SOFFICE_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return ""
        outputs = sorted(outdir.glob("*.txt"))
        if not outputs:
            return ""
        return _read_text(outputs[0])


def extract(path, max_chars=MAX_CHARS):
    """Extract one document. Returns ``{"text": str, "engine": str}``."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXT or not path.is_file():
        return {"text": "", "engine": ""}
    if ext == ".pdf":
        raw = str(path)
        attempts = []
        if _module_available("pypdf"):
            attempts.append(("pypdf", lambda: _pdf_with_pypdf(raw)))
        if _module_available("fitz"):
            attempts.append(("pymupdf", lambda: _pdf_with_pymupdf(raw)))
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            attempts.append(("pdftotext", lambda: _cli_text(
                pdftotext, ["-layout", "-enc", "UTF-8", raw, "-"])))
        mutool = shutil.which("mutool")
        if mutool:
            attempts.append(("mutool", lambda: _cli_text(
                mutool, ["draw", "-F", "txt", "-o", "-", raw])))
        for name, fn in attempts:
            try:
                text = _normalize(fn())
            except Exception:
                text = ""
            if text:
                return {"text": _cap(text, max_chars), "engine": name}
        return {"text": "", "engine": ""}
    if ext == ".rtf":
        try:
            text = _normalize(_rtf_to_text(path.read_bytes()[:MAX_ENGINE_BYTES]))
        except OSError:
            text = ""
        if text:
            return {"text": _cap(text, max_chars), "engine": "rtf-parser"}
        text = _normalize(_soffice_convert(path))
        return {"text": _cap(text, max_chars), "engine": "libreoffice"} if text else \
            {"text": "", "engine": ""}
    text = _normalize(_soffice_convert(path))
    return {"text": _cap(text, max_chars), "engine": "libreoffice"} if text else \
        {"text": "", "engine": ""}


def extract_text(path, max_chars=MAX_CHARS):
    """Convenience wrapper returning text or ``None``."""
    return extract(path, max_chars=max_chars).get("text") or None
