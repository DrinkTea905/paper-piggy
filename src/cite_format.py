# -*- coding: utf-8 -*-
"""
引注格式引擎（《法学引注手册》子集）——规则做格式，绝不交 LLM（蓝图铁律二）。
两种形态：
 1) 资料汇编紧凑式：[{作者}《{题名}》，{刊名}，第{印刷页}页]  —— 第二部分正文用。
 2) 完整手册脚注式：{作者}：《{题名}》，载《{刊名}》{年}年第{期}期，第{印刷页}页。
默认不加引领词（参见/见/转引自 是对"是否核实原文"的实质声明，须人判，不擅自加）。
EN-L2：按 itemtype 分派模板——statute→「《法名》（YYYY年）第X条」、report→
「机构：《报告名》（YYYY年），第X页」；itemtype 不认识时回退期刊式（老行为不变）。
"""
import re
import document_formats as DF

# EN-L2：条号（汉字数字或阿拉伯数字），从命中 chunk 的 heading 里抽（EN-L1 切块时已置为条号）
_RE_ARTICLE = re.compile(r'第[一二三四五六七八九十百千零〇\d]+条')


def _statute_cite(hit, heading=""):
    """EN-L2：法源引注「《法名》（YYYY年）第X条」（《法学引注手册》法律文件基本式的骨架）。
       法名取 title（zotero_source 已把 statute 的 nameOfAct 映射到 title）；
       条号优先用调用方传入的 heading（server /cite 按命中 chunk 传），退回 hit 自带 heading，
       都没有则省略条号、整体引用；title 里已带年份版本（如"（2018年修正）"）时不再重复注年。"""
    name = _clean_title(hit.get("title"))
    s = f"《{name}》" if name else ""
    yr = str(hit.get("year") or "").strip()
    if yr and yr not in (hit.get("title") or ""):
        s += f"（{yr}年）"
    m = _RE_ARTICLE.search(heading or hit.get("heading") or "")
    if m:
        s += m.group(0)
    return s


def _locator(hit):
    fmt = (hit.get("fulltext_format") or ("pdf" if hit.get("has_pdf") else "")).lower()
    if fmt and fmt != "pdf":
        return hit.get("locator") or DF.locator_label(fmt, hit.get("page"), hit.get("heading"))
    pg = _printed_display(hit.get("key"), hit.get("page")) or (hit.get("official_pages") or "")
    return f"第{pg}页" if pg else ""


def _report_cite(hit):
    """EN-L2：报告/白皮书引注「机构：《报告名》（YYYY年），第X页」。
       机构取 author 首位（Zotero report 的机构作者通常填在 creators），
       缺则退 journal 位（folder 模式 AI 抽取常把发布机构放刊名字段）。"""
    org = _first_author(hit.get("author")) or (hit.get("journal") or "").strip()
    title = _clean_title(hit.get("title"))
    yr = str(hit.get("year") or "").strip()
    s = (f"{org}：" if org else "") + (f"《{title}》" if title else "")
    if yr and yr not in (hit.get("title") or ""):
        s += f"（{yr}年）"
    loc = _locator(hit)
    if loc:
        s += f"，{loc}"
    return s


def _first_author(author):
    a = (author or "").split(";")[0].strip()
    return a + ("等" if ";" in (author or "") else "")


def _clean_title(title):
    t = (title or "").strip()
    # 只有题名【整体】被一层《》包裹时才剥掉，否则原样保留。
    # 旧写法 .strip("《》") 会把「《刑法》第201条…」误剥成「刑法》第201条…」，
    # 再被 compact/footnote 外层 f"《{t}》" 包裹后塌成单层《、尾部多出不配平的》。
    if len(t) >= 2 and t.startswith("《") and t.endswith("》") and t.count("《") == 1 and t.count("》") == 1:
        t = t[1:-1]
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


def compact(hit, heading=""):
    """资料汇编紧凑式引注。hit：检索结果 dict（含 key/author/title/journal/page/official_pages）。
       返回 '[作者《题名》，刊名，第印刷页页]'。缺项自动省略。
       EN-L2：heading 为可选新参（不动原参数序，老调用零改动）——statute 时用来传条号；
       statute/report 走专用模板，其余 itemtype（含不认识的）回退期刊式。"""
    it = (hit.get("itemtype") or "").strip()
    if it == "statute":
        return f"[{_statute_cite(hit, heading)}]"
    if it == "report":
        return f"[{_report_cite(hit)}]"
    author = _first_author(hit.get("author"))
    title = _clean_title(hit.get("title"))
    journal = (hit.get("journal") or "").strip()
    loc = _locator(hit)
    parts = []
    if author:
        parts.append(author)
    parts.append(f"《{title}》" if title else "")
    inner = "".join(parts)
    if journal:
        inner += f"，{journal}"
    if loc:
        inner += f"，{loc}"
    return f"[{inner}]"


def footnote(hit, year="", issue="", heading=""):
    """完整手册脚注式：作者：《题名》，载《刊名》年年第期期，第印刷页页。
       期号从 page_map 的 issue 解析（无则标"待补期号"）。默认无引领词。
       EN-L2：heading 为可选新参（追加在末位，老调用零改动）——statute 传条号；
       statute/report 走专用模板并以句号收尾，其余 itemtype 回退期刊式。"""
    it = (hit.get("itemtype") or "").strip()
    if it == "statute":
        return _statute_cite(hit, heading) + "。"
    if it == "report":
        return _report_cite(hit) + "。"
    author = _first_author(hit.get("author"))
    title = _clean_title(hit.get("title"))
    journal = (hit.get("journal") or "").strip()
    yr = str(year or hit.get("year") or "").strip()
    loc = _locator(hit)
    # 期号：优先传入；仅 PDF 能从页码映射补全
    iss = issue
    fulltext_format = str(
        hit.get("fulltext_format") or ("pdf" if hit.get("has_pdf") else "")
    ).strip().lower()
    if not iss and fulltext_format == "pdf":
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
    if loc:
        s += f"，{loc}"
    s += "。"
    return s
