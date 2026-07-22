# -*- coding: utf-8 -*-
"""深索全文附件提取状态的结构化 sidecar。

旧版只有 ``state/deep_no_text.txt`` 一个集合，因而把附件丢失、坏 PDF 和真扫描件
都显示成「扫描件」。本文件保留逐篇、机器可读的真实终态；旧集合仅作旧前端兼容。
"""
import json
import os
import threading
import time

import config as C


STATUS_FILE = C.STATE / "deep_extract_status.json"
VALID_STATUSES = frozenset({
    "missing_pdf", "invalid_pdf", "ocr_pending", "ocr_failed",
    "missing_file", "invalid_file", "ok_native", "ok_ocr", "ok_text",
})
_LOCK = threading.Lock()


def load_items():
    """返回 ``{stem: status_record}``；文件缺失或损坏时安全退为空。"""
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return dict(items) if isinstance(items, dict) else {}
    except Exception:
        return {}


def get(stem):
    """读取一篇的状态；未知时返回空字典。"""
    return load_items().get(str(stem), {})


def _write(items):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, STATUS_FILE)


def set_status(stem, status, **details):
    """原子更新一篇状态并返回写入的记录。"""
    if status not in VALID_STATUSES:
        raise ValueError(f"未知全文提取状态：{status}")
    rec = {"status": status}
    for key in ("error", "total_pages", "native_pages", "ocr_pages",
                "empty_pages", "ocr_confidence"):
        if key in details:
            rec[key] = details[key]
    rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        items = load_items()
        items[str(stem)] = rec
        _write(items)
    return rec


def remove(stems):
    """移除若干篇状态（附件替换/用户显式重试时调用）。"""
    stems = {str(x) for x in (stems or []) if x}
    if not stems:
        return 0
    with _LOCK:
        items = load_items()
        before = len(items)
        for stem in stems:
            items.pop(stem, None)
        if len(items) != before:
            _write(items)
        return before - len(items)


def counts(items=None):
    """按状态计数，所有合法状态恒有整数键。"""
    out = {status: 0 for status in sorted(VALID_STATUSES)}
    for rec in (items if items is not None else load_items()).values():
        status = rec.get("status") if isinstance(rec, dict) else None
        if status in out:
            out[status] += 1
    return out


def reconcile_legacy():
    """迁移旧 ``deep_no_text.txt``，并解除可 OCR 条目的粘性排除。

    旧集合没有失败类型，只能回看 ``extracted/<stem>.json`` 的 error。迁移后：
    - 无附件 / 坏 PDF 分别落 ``missing_pdf`` / ``invalid_pdf``；
    - 无文字层或无法判明的旧条目落 ``ocr_pending``；
    - 只有已经明确 ``ocr_failed`` 的新条目继续留在旧集合，避免自动空转。
    返回本次新增/更新的状态条数。
    """
    legacy = C.STATE / "deep_no_text.txt"
    if not legacy.exists():
        return 0
    with _LOCK:
        stems = {x for x in legacy.read_text(encoding="utf-8").split() if x}
        if not stems:
            return 0
        items = load_items()
        changed = 0
        for stem in stems:
            old = items.get(stem) if isinstance(items.get(stem), dict) else {}
            if old.get("status") in VALID_STATUSES:
                continue
            rec = {}
            try:
                rec = json.loads((C.EXTRACTED / f"{stem}.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            err = str(rec.get("error") or "")
            if err in ("no_pdf_on_disk", "no_source_on_disk"):
                status = "missing_pdf"
            elif ("PdfiumError" in err or "data format" in err.lower()
                  or "invalid pdf" in err.lower()):
                status = "invalid_pdf"
            else:
                status = "ocr_pending"
            items[stem] = {
                "status": status,
                "error": err,
                "total_pages": len(rec.get("pages") or []),
                "native_pages": len(rec.get("pages") or []) if rec.get("ok") else 0,
                "ocr_pages": 0,
                "empty_pages": 0,
                "ocr_confidence": None,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            changed += 1
        if changed:
            _write(items)
        # ocr_pending 必须从旧排除集合移走，升级后才能重新进入深索候选；附件/坏 PDF
        # 也由结构化状态单独展示，不再冒充扫描件。真正 ocr_failed 仍留作旧前端兼容。
        keep = [stem for stem in stems
                if (items.get(stem) or {}).get("status") == "ocr_failed"]
        legacy.write_text("".join(stem + "\n" for stem in sorted(keep)), encoding="utf-8")
        return changed
