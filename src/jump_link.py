# -*- coding: utf-8 -*-
"""
直达原文/Zotero 跳页辅助：
- 在 AI 对话回复、检索来源列表、引文定位、论断核验等交互输出中生成本机可点击链接
  (`http://127.0.0.1:<port>/jump?key=...&page=...`)，点击后由本地服务唤起 Zotero
  打开对应 PDF 的准确页码（或选中条目 / 文件夹模式打开本地文件）。
- 仅用于对话沟通与核验追溯；正式论文成稿 .docx、wiki 综述正文与导出表格中绝不写入。
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
import config as C

_ZOTERO_KEY_RE = re.compile(r"^[A-Za-z0-9]{8}$")
_STORAGE_KEY_RE = re.compile(r"[/\\]storage[/\\]([A-Za-z0-9]{8})[/\\]")
_WIKI_PREFIXES = ("answer-", "concept-", "topic-", "digest-", "outline-", "entity-", "overview-")


def _clean(v):
    return str(v or "").strip()


def _positive_page(page):
    if page in (None, "", 0, False):
        return None
    try:
        n = int(page)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def is_wiki_key(key):
    k = _clean(key)
    return bool(k and k.startswith(_WIKI_PREFIXES))


def is_zotero_key(key):
    k = _clean(key)
    return bool(k and _ZOTERO_KEY_RE.fullmatch(k))


def extract_zotero_att_key(paper):
    """提取 Zotero PDF 附件的 8 位 item key。
    优先读索引时保留的 pdf_attachment_key；旧索引无此字段时从 storage/<KEY>/...pdf 路径反推，
    确保老库无需重建索引即可直接跳页。"""
    if not isinstance(paper, dict):
        return ""
    explicit = _clean(paper.get("pdf_attachment_key"))
    if _ZOTERO_KEY_RE.fullmatch(explicit):
        return explicit.upper()
    candidates = [
        paper.get("pdf_path"),
        paper.get("fulltext_path"),
        paper.get("pdf"),
    ]
    for att in (paper.get("fulltext_attachments") or []):
        if isinstance(att, dict) and str(att.get("format") or "").lower() == "pdf":
            explicit_att = _clean(att.get("att_key"))
            if _ZOTERO_KEY_RE.fullmatch(explicit_att):
                return explicit_att.upper()
            candidates.append(att.get("path"))
    for path in candidates:
        m = _STORAGE_KEY_RE.search(str(path or ""))
        if m:
            return m.group(1).upper()
    return ""


def _statute_body_path(key):
    """若为独立法规库 STAT- 键，尝试定位本地已核验法规正文 body.md。"""
    k = _clean(key)
    if not k.startswith("STAT-"):
        return ""
    try:
        base = C.DATA / "statutes"
        if not base.exists():
            return ""
        cand = base / k / "body.md"
        if cand.is_file():
            return str(cand)
        for d in base.iterdir():
            if not d.is_dir():
                continue
            mf = d / "metadata.json"
            if mf.is_file():
                import json
                meta = json.loads(mf.read_text(encoding="utf-8"))
                if _clean(meta.get("key")) == k and (d / "body.md").is_file():
                    return str(d / "body.md")
    except Exception:
        pass
    return ""


def resolve_open_target(paper, key="", pdf_page=None):
    """把文献记录 + 可选页码解析为可打开目标 (kind, target)。
    kind ∈ {'zotero', 'file', None}:
      1. Zotero 有 PDF 附件键且主/请求为 PDF → zotero://open-pdf/library/items/<attKey>[?page=<n>]
      2. Zotero 条目键(8位)但无 PDF 附件键 → zotero://select/library/items/<itemKey>
      3. 文件夹模式或链接附件本地文件 → 本地文件绝对路径
      4. 独立法规库 → body.md 路径
    """
    paper = paper if isinstance(paper, dict) else {}
    k = _clean(key or paper.get("key"))
    page_num = _positive_page(pdf_page)
    att_key = extract_zotero_att_key(paper)
    fmt = _clean(paper.get("fulltext_format") or ("pdf" if paper.get("has_pdf") else "")).lower()

    if att_key and (fmt == "pdf" or paper.get("has_pdf") or paper.get("pdf_path") or page_num):
        url = f"zotero://open-pdf/library/items/{att_key}"
        if page_num is not None:
            url += f"?page={page_num}"
        return "zotero", url

    for cand in (paper.get("fulltext_path"), paper.get("pdf_path"), paper.get("pdf")):
        p = _clean(cand)
        if p and Path(p).exists():
            # 若它是 Zotero 条目却没有 PDF 附件（如 EPUB/链接附件），有 Zotero key 也可打开文件；
            # 若文件存在直接返回本地文件，除非调用方没文件路径时再退回 select 条目。
            return "file", str(Path(p))

    if is_zotero_key(k):
        return "zotero", f"zotero://select/library/items/{k}"

    st_path = _statute_body_path(k)
    if st_path:
        return "file", st_path

    return None, ""


def make_jump_url(key, pdf_page=None, base_url=None):
    """构造可供任何 AI 聊天 Markdown 直接点击的本机 HTTP 跳转链接。"""
    k = _clean(key)
    if not k or is_wiki_key(k):
        return ""
    base = (_clean(base_url) or C.DAEMON_URL).rstrip("/")
    page_num = _positive_page(pdf_page)
    qs = f"key={quote(k, safe='')}"
    if page_num is not None:
        qs += f"&page={page_num}"
    return f"{base}/jump?{qs}"
