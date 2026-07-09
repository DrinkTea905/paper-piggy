# -*- coding: utf-8 -*-
"""
引注格式引擎（《法学引注手册》子集）——规则做格式，绝不交 LLM（蓝图铁律二）。
两种形态：
 1) 资料汇编紧凑式：[{作者}《{题名}》，{刊名}，第{印刷页}页]  —— 第二部分正文用。
 2) 完整手册脚注式：{作者}：《{题名}》，载《{刊名}》{年}年第{期}期，第{印刷页}页。
默认不加引领词（参见/见/转引自 是对"是否核实原文"的实质声明，须人判，不擅自加）。
"""
import re


def _first_author(author):
    a = (author or "").split(";")[0].strip()
    return a + ("等" if ";" in (author or "") else "")


def _clean_title(title):
    t = (title or "").strip().strip("《》")
    return t


def _printed_display(key, pdf_page):
    """取该篇该 PDF 页的印刷页码显示串（高置信直接数字；推算标"（页码推算）"）。"""
    if pdf_page is None or not key:
        return ""
    try:
        import page_map as PM
        return PM.printed(key, pdf_page).get("display") or ""
    except Exception:
        return ""


def compact(hit):
    """资料汇编紧凑式引注。hit：检索结果 dict（含 key/author/title/journal/page/official_pages）。
       返回 '[作者《题名》，刊名，第印刷页页]'。缺项自动省略。"""
    author = _first_author(hit.get("author"))
    title = _clean_title(hit.get("title"))
    journal = (hit.get("journal") or "").strip()
    pg = _printed_display(hit.get("key"), hit.get("page")) or (hit.get("official_pages") or "")
    parts = []
    if author:
        parts.append(author)
    parts.append(f"《{title}》" if title else "")
    inner = "".join(parts)
    if journal:
        inner += f"，{journal}"
    if pg:
        inner += f"，第{pg}页"
    return f"[{inner}]"


def footnote(hit, year="", issue=""):
    """完整手册脚注式：作者：《题名》，载《刊名》年年第期期，第印刷页页。
       期号从 page_map 的 issue 解析（无则标"待补期号"）。默认无引领词。"""
    author = _first_author(hit.get("author"))
    title = _clean_title(hit.get("title"))
    journal = (hit.get("journal") or "").strip()
    yr = str(year or hit.get("year") or "").strip()
    pg = _printed_display(hit.get("key"), hit.get("page")) or (hit.get("official_pages") or "")
    # 期号：优先传入，否则从 page_map issue 解析
    iss = issue
    if not iss:
        try:
            import page_map as PM
            iss = (PM.printed(hit.get("key"), hit.get("page")) or {}).get("issue") or ""
        except Exception:
            iss = ""
    m = re.search(r"第\s*([0-9]{1,2})\s*期", iss)
    period = m.group(1) if m else ""
    s = ""
    if author:
        s += f"{author}："
    s += f"《{title}》" if title else ""
    if journal:
        s += f"，载《{journal}》"
    if yr:
        s += f"{yr}年"
    if period:
        s += f"第{period}期"
    elif journal:
        s += "第__期（待补期号）"
    if pg:
        s += f"，第{pg}页"
    s += "。"
    return s
