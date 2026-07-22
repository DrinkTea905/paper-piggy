# -*- coding: utf-8 -*-
"""PaperPiggy 可读取的全文附件格式与兼容字段。

这一处是格式清单、优先级与题录附件字段的唯一事实源。HTML 刻意不支持：Zotero
网页快照不能作为“有全文附件”的入库依据。
"""
from pathlib import Path


FORMAT_PRIORITY = ("pdf", "epub", "docx", "markdown", "txt")
FORMAT_RANK = {fmt: i for i, fmt in enumerate(FORMAT_PRIORITY)}
FORMAT_LABELS = {
    "pdf": "PDF",
    "epub": "EPUB",
    "docx": "DOCX",
    "markdown": "Markdown",
    "txt": "TXT",
}
FORMAT_EXTENSIONS = {
    "pdf": frozenset({".pdf"}),
    "epub": frozenset({".epub"}),
    "docx": frozenset({".docx"}),
    "markdown": frozenset({".md", ".markdown"}),
    "txt": frozenset({".txt"}),
}
SUPPORTED_EXTENSIONS = frozenset(
    ext for extensions in FORMAT_EXTENSIONS.values() for ext in extensions
)

_MIME_FORMATS = {
    "application/pdf": "pdf",
    "application/epub+zip": "epub",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/plain": "txt",
}


def detect_format(file_path="", content_type=""):
    """由扩展名优先、MIME 兜底识别支持格式；未知/HTML 返回空串。"""
    suffix = Path(str(file_path or "")).suffix.lower()
    for fmt, extensions in FORMAT_EXTENSIONS.items():
        if suffix in extensions:
            return fmt
    return _MIME_FORMATS.get(str(content_type or "").split(";", 1)[0].strip().lower(), "")


def format_label(fmt):
    return FORMAT_LABELS.get(str(fmt or "").lower(), str(fmt or "").upper())


def supported_file(path):
    return bool(detect_format(path))


def sort_attachments(attachments):
    """按 PDF→EPUB→DOCX→Markdown→TXT 稳定排序并去掉重复路径。"""
    out, seen = [], set()
    for order, raw in enumerate(attachments or []):
        if isinstance(raw, str):
            raw = {"path": raw}
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        fmt = str(raw.get("format") or detect_format(path, raw.get("content_type"))).lower()
        if not path or fmt not in FORMAT_RANK:
            continue
        norm = str(Path(path)).casefold()
        if norm in seen:
            continue
        seen.add(norm)
        out.append({"format": fmt, "path": path, "order": order})
    out.sort(key=lambda x: (FORMAT_RANK[x["format"]], x["order"]))
    return [{"format": x["format"], "path": x["path"]} for x in out]


def primary_attachment(attachments):
    ordered = sort_attachments(attachments)
    return ordered[0] if ordered else None


def apply_attachment_fields(record, attachments):
    """把通用全文字段写入题录，并保留语义准确的 PDF 兼容字段。"""
    ordered = sort_attachments(attachments)
    primary = ordered[0] if ordered else None
    pdf = next((a for a in ordered if a["format"] == "pdf"), None)
    record["fulltext_attachments"] = ordered
    record["has_fulltext"] = bool(primary)
    record["fulltext_path"] = primary["path"] if primary else ""
    record["fulltext_format"] = primary["format"] if primary else ""
    record["has_pdf"] = bool(pdf)
    record["pdf_path"] = pdf["path"] if pdf else ""
    return record


def normalize_record(record):
    """兼容旧 papers.jsonl：只有 has_pdf/pdf_path 的记录自动视为 PDF 全文。"""
    if not isinstance(record, dict):
        return record
    attachments = list(record.get("fulltext_attachments") or [])
    ft_path = str(record.get("fulltext_path") or "").strip()
    if ft_path:
        attachments.append({"path": ft_path, "format": record.get("fulltext_format") or ""})
    pdf_path = str(record.get("pdf_path") or "").strip()
    if pdf_path:
        attachments.append({"path": pdf_path, "format": "pdf"})
    # 极旧记录可能只有 has_pdf=True 而没有路径。保留布尔真值，但无法深索。
    if attachments:
        return apply_attachment_fields(record, attachments)
    record.setdefault("fulltext_attachments", [])
    record.setdefault("has_fulltext", bool(record.get("has_pdf")))
    record.setdefault("fulltext_path", "")
    record.setdefault("fulltext_format", "pdf" if record.get("has_pdf") else "")
    record.setdefault("has_pdf", False)
    record.setdefault("pdf_path", "")
    return record


def locator_label(fmt, position=None, heading="", explicit=""):
    """生成人类可读位置；非 PDF 绝不伪装成页码。"""
    if explicit:
        return str(explicit)
    fmt = str(fmt or "").lower()
    heading = str(heading or "").strip()
    if fmt == "pdf":
        return f"PDF 第 {position} 页" if position else "PDF"
    if heading:
        return heading
    unit = {"epub": "章节", "docx": "节", "markdown": "节", "txt": "段落"}.get(fmt, "位置")
    return f"第 {position} {unit}" if position else format_label(fmt)
