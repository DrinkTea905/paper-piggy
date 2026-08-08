# -*- coding: utf-8 -*-
"""
引注格式引擎（《法学引注手册》子集）——规则做格式，绝不交 LLM。

creator 角色由 Zotero 题录保留；格式化按 itemtype 明确分派：
journalArticle / book / bookSection / report / thesis / statute。
未知类型继续回退期刊式，以兼容旧记录。
"""
import re

import document_formats as DF


_RE_ARTICLE = re.compile(r"第[一二三四五六七八九十百千零〇\d]+条")


def _clean_title(title):
    t = (title or "").strip()
    if (len(t) >= 2 and t.startswith("《") and t.endswith("》")
            and t.count("《") == 1 and t.count("》") == 1):
        return t[1:-1]
    return t


def _split_names(value):
    return [x.strip() for x in str(value or "").split(";") if x.strip()]


def creator_names(hit, role):
    """按 creator 原顺序返回指定角色的人名/机构名；兼容没有 creators 的旧索引。"""
    creators = hit.get("creators")
    if isinstance(creators, list):
        names = [
            str(c.get("name") or "").strip()
            for c in creators
            if isinstance(c, dict) and str(c.get("role") or "").strip() == role
        ]
        names = [x for x in names if x]
        if names:
            return names
    field = {
        "author": "authors",
        "editor": "editors",
        "translator": "translators",
    }.get(role, "")
    value = hit.get(field) if field else ""
    if role == "author" and not value:
        value = hit.get("author")
    return _split_names(value)


def _join_names(names):
    """两人全部列出，三人以上按中文法学体例缩写为首位姓名加“等”。"""
    cleaned = [x for x in names if x]
    if len(cleaned) >= 3:
        return cleaned[0] + "等"
    return "、".join(cleaned)


def _first_author(hit):
    """期刊作者同样遵守“两人全列、三人以上用等”。"""
    authors = creator_names(hit, "author")
    return _join_names(authors)


def _printed_display(key, pdf_page):
    if pdf_page is None or not key:
        return ""
    try:
        import page_map as PM
        return PM.printed(key, pdf_page).get("display") or ""
    except Exception:
        return ""


def _locator(hit):
    fmt = (hit.get("fulltext_format") or ("pdf" if hit.get("has_pdf") else "")).lower()
    if fmt and fmt != "pdf":
        return hit.get("locator") or DF.locator_label(
            fmt, hit.get("page"), hit.get("heading")
        )
    pg = _printed_display(hit.get("key"), hit.get("page")) or (
        hit.get("official_pages") or ""
    )
    return f"第{pg}页" if pg else ""


def issue_of(hit, issue=""):
    """期号优先取显式参数/题录字段，PDF 再退回页码映射。"""
    raw = str(issue or hit.get("issue") or "").strip()
    if not raw:
        fmt = str(
            hit.get("fulltext_format") or ("pdf" if hit.get("has_pdf") else "")
        ).strip().lower()
        if fmt == "pdf":
            try:
                import page_map as PM
                raw = str(
                    (PM.printed(hit.get("key"), hit.get("page")) or {}).get("issue")
                    or ""
                ).strip()
            except Exception:
                raw = ""
    m = re.search(r"第?\s*([0-9]{1,3})\s*期?", raw)
    return m.group(1) if m else raw


def _publication(hit):
    place = str(hit.get("place") or "").strip()
    publisher = str(hit.get("publisher") or "").strip()
    year = str(hit.get("year") or "").strip()
    parts = []
    if place and publisher:
        parts.append(f"{place}：{publisher}")
    elif publisher:
        parts.append(publisher)
    elif place:
        parts.append(place)
    if year:
        parts.append(f"{year}年")
    return "，".join(parts)


def _book_publication(hit):
    """书籍/书章出版项：出版社＋年份/版次；缺项直接省略，由 missing_fields 提示。"""
    publisher = str(hit.get("publisher") or "").strip()
    year = str(hit.get("year") or "").strip()
    edition = str(hit.get("edition") or "").strip()
    text = publisher
    if year:
        text += f"{year}年"
    if edition:
        if re.fullmatch(r"\d+", edition):
            edition = f"第{edition}版"
        elif not edition.endswith("版"):
            edition += "版"
        text += edition
    elif year:
        text += "版"
    return text


def _translated_author(name):
    """把 Zotero 中常见的 [美]/【美】国别前缀规范为中文引注用的〔美〕。"""
    raw = str(name or "").strip()
    match = re.match(r"^[\[【]([^\]】]+)[\]】]\s*(.+)$", raw)
    if not match:
        return raw
    return f"〔{match.group(1).strip()}〕{match.group(2).strip()}"


def _statute_cite(hit, heading=""):
    name = _clean_title(hit.get("title"))
    text = f"《{name}》" if name else ""
    version_label = str(hit.get("statute_version_label") or "").strip()
    year = str(hit.get("year") or "").strip()
    if version_label:
        text += f"（{version_label}）"
    elif year and year not in (hit.get("title") or ""):
        text += f"（{year}年）"
    match = _RE_ARTICLE.search(heading or hit.get("heading") or "")
    if match:
        text += match.group(0)
    return text


def _journal_cite(hit, year="", issue="", compact_style=False):
    author = _first_author(hit)
    translators = creator_names(hit, "translator")
    title = _clean_title(hit.get("title"))
    journal = str(hit.get("journal") or "").strip()
    yr = str(year or hit.get("year") or "").strip()
    locator = _locator(hit)
    if compact_style:
        text = (author if author else "") + (f"《{title}》" if title else "")
        if translators:
            text += f"，{_join_names(translators)}译"
        if journal:
            text += f"，{journal}"
        if locator:
            text += f"，{locator}"
        return text
    text = f"{author}：" if author else ""
    text += f"《{title}》" if title else ""
    if translators:
        text += f"，{_join_names(translators)}译"
    if journal:
        text += f"，载《{journal}》"
    if yr:
        text += f"{yr}年"
    period = issue_of(hit, issue)
    if period:
        text += f"第{period}期"
    if locator:
        text += f"，{locator}"
    return text


def _book_cite(hit):
    authors = creator_names(hit, "author")
    editors = creator_names(hit, "editor")
    translators = creator_names(hit, "translator")
    title = _clean_title(hit.get("title"))
    if authors:
        display_authors = [_translated_author(x) for x in authors] if translators else authors
        text = f"{_join_names(display_authors)}："
    elif editors:
        text = f"{_join_names(editors)}主编："
    else:
        text = ""
    text += f"《{title}》" if title else ""
    if translators:
        text += f"，{_join_names(translators)}译"
    publication = _book_publication(hit)
    if publication:
        text += f"，{publication}"
    locator = _locator(hit)
    if locator:
        text += f"，{locator}"
    return text


def _book_section_cite(hit):
    authors = creator_names(hit, "author")
    editors = creator_names(hit, "editor")
    translators = creator_names(hit, "translator")
    chapter = _clean_title(hit.get("title"))
    book = _clean_title(hit.get("book_title") or hit.get("journal"))
    text = f"{_join_names(authors)}：" if authors else ""
    text += f"《{chapter}》" if chapter else ""
    if book:
        if editors:
            text += f"，载{_join_names(editors)}主编：《{book}》"
        else:
            text += f"，载《{book}》"
    if translators:
        text += f"，{_join_names(translators)}译"
    publication = _book_publication(hit)
    if publication:
        text += f"，{publication}"
    locator = _locator(hit)
    if locator:
        text += f"，{locator}"
    return text


def _report_cite(hit):
    authors = creator_names(hit, "author")
    institution = str(hit.get("institution") or "").strip()
    title = _clean_title(hit.get("title"))
    report_number = str(hit.get("report_number") or "").strip()
    year = str(hit.get("year") or "").strip()
    place = str(hit.get("place") or "").strip()
    text = f"{_join_names(authors)}：" if authors else (
        f"{institution}：" if institution else ""
    )
    text += f"《{title}》" if title else ""
    if report_number:
        text += f"（{report_number}）"
    publisher_part = ""
    if institution and institution not in authors:
        publisher_part = f"{place}：{institution}" if place else institution
    elif place:
        publisher_part = place
    if publisher_part:
        text += f"，{publisher_part}"
    if year:
        text += f"，{year}年"
    locator = _locator(hit)
    if locator:
        text += f"，{locator}"
    return text


def _thesis_cite(hit):
    authors = creator_names(hit, "author")
    title = _clean_title(hit.get("title"))
    university = str(hit.get("university") or hit.get("institution") or "").strip()
    thesis_type = str(hit.get("thesis_type") or "").strip()
    year = str(hit.get("year") or "").strip()
    text = f"{_join_names(authors)}：" if authors else ""
    text += f"《{title}》" if title else ""
    degree = f"{thesis_type}学位论文" if thesis_type else "学位论文"
    if university:
        text += f"，{university}{degree}"
    else:
        text += f"，{degree}"
    if year:
        text += f"，{year}年"
    locator = _locator(hit)
    if locator:
        text += f"，{locator}"
    return text


def _format_by_type(hit, year="", issue="", heading="", compact_style=False):
    itemtype = str(hit.get("itemtype") or "").strip()
    if itemtype == "statute":
        return _statute_cite(hit, heading)
    if itemtype == "book":
        return _book_cite(hit)
    if itemtype == "bookSection":
        return _book_section_cite(hit)
    if itemtype == "report":
        return _report_cite(hit)
    if itemtype == "thesis":
        return _thesis_cite(hit)
    return _journal_cite(hit, year=year, issue=issue, compact_style=compact_style)


def compact(hit, heading=""):
    return f"[{_format_by_type(hit, heading=heading, compact_style=True)}]"


def footnote(hit, year="", issue="", heading=""):
    return _format_by_type(hit, year=year, issue=issue, heading=heading) + "。"


def missing_fields(hit):
    """按条目类型返回真正适用的缺失字段，不向书章/图书/报告索要期刊字段。"""
    itemtype = str(hit.get("itemtype") or "").strip()
    missing = []
    authors = creator_names(hit, "author")
    editors = creator_names(hit, "editor")
    locator = _locator(hit)
    if itemtype == "statute":
        if not str(hit.get("title") or "").strip():
            missing.append("title")
        if (not str(hit.get("statute_version_label") or "").strip()
                and not str(hit.get("year") or "").strip()
                and not re.search(r"\d{4}", str(hit.get("title") or ""))):
            missing.append("year")
    elif itemtype == "book":
        if not (authors or editors):
            missing.append("author/editor")
        for field in ("title", "publisher", "year"):
            if not str(hit.get(field) or "").strip():
                missing.append(field)
    elif itemtype == "bookSection":
        if not authors:
            missing.append("author")
        if not str(hit.get("title") or "").strip():
            missing.append("title")
        if not editors:
            missing.append("editor")
        if not str(hit.get("book_title") or hit.get("journal") or "").strip():
            missing.append("book_title")
        for field in ("publisher", "year"):
            if not str(hit.get(field) or "").strip():
                missing.append(field)
        if not locator:
            missing.append("page")
    elif itemtype == "report":
        if not (authors or str(hit.get("institution") or "").strip()):
            missing.append("author/institution")
        if not str(hit.get("title") or "").strip():
            missing.append("title")
        if not str(hit.get("year") or "").strip():
            missing.append("year")
    elif itemtype == "thesis":
        if not authors:
            missing.append("author")
        if not str(hit.get("title") or "").strip():
            missing.append("title")
        if not str(hit.get("university") or hit.get("institution") or "").strip():
            missing.append("university")
        if not str(hit.get("year") or "").strip():
            missing.append("year")
    else:
        for field in ("author", "title", "journal", "year"):
            value = authors if field == "author" else hit.get(field)
            if not value:
                missing.append(field)
        if str(hit.get("journal") or "").strip() and not issue_of(hit):
            missing.append("issue")
        if not locator:
            missing.append("page")
    return missing
