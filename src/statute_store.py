# -*- coding: utf-8 -*-
"""本地法规版本库。

Agent 负责从官方网页取得并核对正文；本模块负责域名/结构校验、两次确认令牌、
不可覆盖的原文快照、版本状态与统一题录记录。它只写 ``data/statutes``，绝不写 Zotero。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import config as C
import document_formats as DF


OFFICIAL_DOMAIN_SUFFIXES = (
    "gov.cn",
    "court.gov.cn",
    "spp.gov.cn",
)
VALID_STATUSES = {"尚未施行", "现行有效", "已修订", "已废止"}
_ARTICLE_RE = re.compile(r"(?m)^\s*(第[一二三四五六七八九十百千零〇\d]+条(?:之[一二三四五六七八九十\d]+)?)")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WRITE_LOCK = threading.RLock()


class StatuteValidationError(ValueError):
    pass


def _clean(value):
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _compact(value):
    return re.sub(r"[\s《》<>（）()【】\[\]·,，。:：;；'\"]+", "", str(value or "")).casefold()


def _normalize_body(body):
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return text + "\n" if text else ""


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text):
    return _sha256_bytes(str(text).encode("utf-8"))


def _official_host(url):
    try:
        parsed = urlsplit(str(url or "").strip())
    except Exception:
        return False, "原文 URL 无法解析"
    if parsed.scheme.lower() != "https":
        return False, "原文 URL 只接受 HTTPS"
    if parsed.username or parsed.password:
        return False, "原文 URL 不得包含账号或密码"
    if parsed.port not in (None, 443):
        return False, "原文 URL 不得使用非标准端口"
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return False, "原文 URL 缺少域名"
    official = any(host == suffix or host.endswith("." + suffix)
                   for suffix in OFFICIAL_DOMAIN_SUFFIXES)
    return official, host


def _validate_date(name, value):
    value = _clean(value)
    if not value:
        return ""
    if not _DATE_RE.fullmatch(value):
        raise StatuteValidationError(f"{name} 必须使用 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise StatuteValidationError(f"{name} 不是有效日期") from exc
    return value


def _normalize_revisions(raw):
    out = []
    for item in raw or []:
        if isinstance(item, str):
            d, label = _validate_date("修订日期", item), ""
        elif isinstance(item, dict):
            d = _validate_date("修订日期", item.get("date"))
            label = _clean(item.get("label"))
        else:
            raise StatuteValidationError("revision_dates 每项必须是日期字符串或 {date, label}")
        if d and d not in {x["date"] for x in out}:
            out.append({"date": d, "label": label})
    return sorted(out, key=lambda x: x["date"])


def _canonical_title(title):
    value = _clean(title).strip("《》")
    value = re.sub(r"[（(]\s*\d{4}\s*年?\s*(?:修订|修正|版本)[^）)]*[）)]\s*$", "", value)
    return value.strip()


def _version_label(payload, revisions, passed_date):
    label = _clean(payload.get("version_label"))
    if label:
        return label
    if revisions:
        return f"{revisions[-1]['date'][:4]}年修订"
    if passed_date:
        return f"{passed_date[:4]}年"
    return ""


def validate_draft(payload):
    """纯校验，不写任何文件；返回规范化草稿与确定性确认令牌。"""
    payload = dict(payload or {})
    title = _clean(payload.get("title")).strip("《》")
    authority = _clean(payload.get("issuing_authority"))
    source_url = str(payload.get("source_url") or "").strip()
    snapshot = str(payload.get("body_markdown") or "")
    body = _normalize_body(snapshot)
    if not title:
        raise StatuteValidationError("缺少法规全称 title")
    if not authority:
        raise StatuteValidationError("缺少制定机关 issuing_authority")
    if not source_url:
        raise StatuteValidationError("缺少官方原文 URL")
    official, host_or_error = _official_host(source_url)
    if not official and not payload.get("confirm_unofficial"):
        raise StatuteValidationError(
            f"{host_or_error} 不在官方域名白名单；如确需采用，必须显式二次确认 confirm_unofficial=true"
        )
    if len(body) < 100:
        raise StatuteValidationError("正文过短，疑似抓取失败或不完整")
    if len(body) > 2_000_000:
        raise StatuteValidationError("正文超过 200 万字节上限，请核对是否误抓整站页面")
    if re.search(r"<(?:html|body|script|style)\b", body, re.I):
        raise StatuteValidationError("body_markdown 仍包含网页 HTML，请提交清理后的法规正文")
    compact_body = _compact(body)
    if _compact(title) not in compact_body:
        raise StatuteValidationError("正文中找不到法规全称，疑似抓错页面或正文残缺")
    articles = _ARTICLE_RE.findall(body)
    if not articles:
        raise StatuteValidationError("正文中未识别到任何行首“第X条”，不得把残缺页面冒充法规原文")
    bad_markers = ("页面不存在", "访问验证", "请输入验证码", "系统繁忙", "Access Denied")
    if any(marker.casefold() in body.casefold() for marker in bad_markers):
        raise StatuteValidationError("正文含访问失败/验证码提示，不得入库")

    passed_date = _validate_date("通过日期", payload.get("passed_date"))
    effective_date = _validate_date("施行日期", payload.get("effective_date"))
    revisions = _normalize_revisions(payload.get("revision_dates"))
    status = _clean(payload.get("validity_status")) or "现行有效"
    if status not in VALID_STATUSES:
        raise StatuteValidationError("validity_status 仅支持：尚未施行、现行有效、已修订、已废止")
    fetched_at_input = _clean(payload.get("fetched_at"))
    fetched_at = fetched_at_input or time.strftime("%Y-%m-%d %H:%M:%S")
    canonical = _canonical_title(title)
    identity = "\n".join((canonical, effective_date, _clean(payload.get("document_number"))))
    key = "STAT-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:14].upper()
    normalized = {
        "schema": 1,
        "key": key,
        "title": title,
        "canonical_title": canonical,
        "short_title": _clean(payload.get("short_title")),
        "issuing_authority": authority,
        "passed_date": passed_date,
        "revision_dates": revisions,
        "revision_count": len(revisions),
        "effective_date": effective_date,
        "legal_level": _clean(payload.get("legal_level")),
        "document_number": _clean(payload.get("document_number")),
        "source_url": source_url,
        "source_host": host_or_error,
        "official_source": bool(official),
        "fetched_at": fetched_at,
        "snapshot_sha256": _sha256_text(snapshot),
        "body_sha256": _sha256_text(body),
        "body_chars": len(body),
        "article_count": len(articles),
        "first_article": articles[0],
        "last_article": articles[-1],
        "statute_version_label": _version_label(payload, revisions, passed_date),
        "declared_status": status,
        "validity_status": status,
        "is_current": status == "现行有效",
        "source_origin": "statute_store",
        "body_markdown": body,
        "snapshot_text": snapshot,
    }
    token_payload = {k: v for k, v in normalized.items()
                     if k not in {"body_markdown", "snapshot_text", "fetched_at"}}
    # 未显式填写时不把每次调用都会变化的当前时刻绑进令牌；一旦 Agent 明确提交抓取时间，
    # 该值就与其它元数据一样必须保持不变。
    token_payload["fetched_at"] = fetched_at_input
    token_payload["body_sha256"] = normalized["body_sha256"]
    token = _sha256_text(json.dumps(token_payload, ensure_ascii=False, sort_keys=True))
    normalized["confirmation_token"] = token
    return normalized


def _record_dir(key):
    return C.STATUTES_DIR / key


def _read_meta(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def load_metadata():
    records = []
    if not C.STATUTES_DIR.exists():
        return records
    for child in sorted(C.STATUTES_DIR.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".tmp-"):
            continue
        meta = _read_meta(child / "metadata.json")
        if meta and meta.get("key") == child.name and (child / "body.md").is_file():
            records.append(meta)
    return records


def _date_key(meta):
    revisions = meta.get("revision_dates") or []
    revision_date = revisions[-1].get("date", "") if revisions else ""
    return (meta.get("effective_date") or revision_date or meta.get("passed_date") or "0000-00-00",
            revision_date, meta.get("passed_date") or "")


def _atomic_json(path, value):
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _recompute_group(canonical_title):
    group = [m for m in load_metadata() if m.get("canonical_title") == canonical_title]
    if not group:
        return []
    today = date.today().isoformat()
    eligible = [m for m in group
                if m.get("declared_status") != "已废止" and _date_key(m)[0] <= today]
    newest_key = max(eligible, key=_date_key).get("key") if eligible else None
    changes = []
    for meta in group:
        old = meta.get("validity_status") or ""
        declared = meta.get("declared_status") or "现行有效"
        when = _date_key(meta)[0]
        if declared == "已废止":
            new = "已废止"
        elif len(group) == 1 and declared in VALID_STATUSES:
            new = declared
        elif when > today:
            new = "尚未施行"
        elif meta.get("key") == newest_key:
            new = "现行有效"
        else:
            new = "已修订"
        meta["validity_status"] = new
        meta["is_current"] = new == "现行有效"
        if new != old:
            _atomic_json(_record_dir(meta["key"]) / "metadata.json", meta)
            changes.append({"key": meta["key"], "title": meta.get("title", ""),
                            "old_status": old, "new_status": new})
    return changes


def add_confirmed(payload, confirmation_token):
    """确认令牌匹配后原子新增一个法规版本；永不覆盖已存在的不同正文。"""
    draft = validate_draft(payload)
    if not confirmation_token or confirmation_token != draft["confirmation_token"]:
        raise StatuteValidationError("确认令牌不匹配；正文或元数据已变化，请重新预览并确认")
    with _WRITE_LOCK:
        target = _record_dir(draft["key"])
        if target.exists():
            existing = _read_meta(target / "metadata.json") or {}
            if existing.get("body_sha256") == draft["body_sha256"]:
                return {"status": "duplicate", "record": existing, "status_changes": []}
            raise StatuteValidationError("同一法规版本 key 已存在但正文哈希不同；为防静默覆盖，已拒绝写入")
        C.STATUTES_DIR.mkdir(parents=True, exist_ok=True)
        temp = C.STATUTES_DIR / (".tmp-" + draft["key"] + "-" + uuid.uuid4().hex)
        temp.mkdir(parents=False, exist_ok=False)
        try:
            (temp / "snapshot.md").write_bytes(draft.pop("snapshot_text").encode("utf-8"))
            (temp / "body.md").write_bytes(draft.pop("body_markdown").encode("utf-8"))
            draft.pop("confirmation_token", None)
            _atomic_json(temp / "metadata.json", draft)
            os.replace(temp, target)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        changes = _recompute_group(draft["canonical_title"])
        saved = _read_meta(target / "metadata.json") or draft
        return {"status": "added", "record": saved, "status_changes": changes}


def record_failure(payload, error):
    """只记录已经确认落盘但校验失败的尝试；预览失败保持零持久写入。"""
    C.STATUTES_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "title": _clean((payload or {}).get("title")),
        "source_url": str((payload or {}).get("source_url") or "").strip(),
        "error": str(error),
    }
    with _WRITE_LOCK, (C.STATUTES_DIR / "failures.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_papers():
    """把已核验法规投影成 papers.jsonl 通用题录；正文走 Markdown 提取而非 PDF/OCR。"""
    out = []
    for meta in load_metadata():
        body = _record_dir(meta["key"]) / "body.md"
        if not body.is_file():
            continue
        try:
            if _sha256_bytes(body.read_bytes()) != meta.get("body_sha256"):
                continue
        except OSError:
            continue
        year_match = re.search(r"\d{4}", meta.get("statute_version_label") or "")
        year = year_match.group(0) if year_match else (meta.get("passed_date") or "")[:4]
        paper = {
            "key": meta["key"], "title": meta.get("title", ""),
            "author": "", "authors": "", "editors": "", "translators": "", "creators": [],
            "year": year, "journal": "", "volume": "", "issue": "", "doi": "", "issn": "",
            "langid": "zh-CN", "keywords": "; ".join(x for x in (
                meta.get("short_title", ""), meta.get("issuing_authority", ""), "法律法规") if x),
            "abstract": "；".join(x for x in (
                meta.get("legal_level", ""), meta.get("document_number", ""), meta.get("validity_status", "")) if x),
            "itemtype": "statute", "url": meta.get("source_url", ""),
            "publisher": "", "place": "", "edition": "", "book_title": "",
            "institution": meta.get("issuing_authority", ""), "official_pages": "", "collections": [],
            "extra": meta.get("validity_status", ""), "ingested_at": meta.get("fetched_at", ""),
            "source_origin": "statute_store", "statute_status": meta.get("validity_status", ""),
            "statute_version_label": meta.get("statute_version_label", ""),
            "statute_meta": {k: meta.get(k) for k in (
                "short_title", "issuing_authority", "passed_date", "revision_dates", "revision_count",
                "effective_date", "legal_level", "document_number", "source_url", "source_host",
                "official_source", "fetched_at", "snapshot_sha256", "body_sha256", "article_count",
                "first_article", "last_article", "validity_status", "is_current")},
        }
        out.append(DF.apply_attachment_fields(paper, [{"format": "markdown", "path": str(body)}]))
    return out
