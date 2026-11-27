# -*- coding: utf-8 -*-
"""
页底脚注与正文分离 + 法学脚注引文挖掘（Milestone 2）：
- 用 PDFium 字符/文本框几何与字号特征，把每页“页底脚注区”与正文分开、展开跨行脚注；
  只懒加载并缓存到 data/footnotes/<stem>.json，绝不要求老库重建索引。
- 解析《法学引注手册》各变体：期刊（有/无「载」）、专著/译著、集刊/文集析出、学位论文、外文文献；
  自动过滤法律、司法解释、行政法规与官方文件，防止把法规当成缺失文献。
"""
import hashlib
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as C

FOOTNOTES_DIR = C.DATA / "footnotes"

# ①–㊿ 圈码 + [1]/〔1〕/上标数字
_CIRCLED_CHARS = (
    "".join(chr(c) for c in range(0x2460, 0x2474))   # ①-⑳
    + "".join(chr(c) for c in range(0x3251, 0x3260)) # ㉑-㉟
    + "".join(chr(c) for c in range(0x32B1, 0x32C0)) # ㊱-㊿
)
_SUP_CHARS = "\u00b2\u00b3\u00b9" + "".join(chr(c) for c in range(0x2070, 0x207A))
_MARKER_RE = re.compile(
    rf"^\s*(?P<marker>[{re.escape(_CIRCLED_CHARS)}]|"
    rf"(?:\[\d{{1,3}}\]|〔\d{{1,3}}〕|（\d{{1,3}}）|\(\d{{1,3}}\)|"
    rf"[{re.escape(_SUP_CHARS)}]+))"
)
_SMALL_NUM_MARKER_RE = re.compile(r"^\s*(?P<marker>\d{1,3})\s+(?=\S)")
_PAGE_FOOTER_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*页|[-—·•]?\s*\d{1,4}\s*[-—·•]?|"
    r"\d{4}\s*年第?\s*\d{1,2}\s*期)\s*$"
)
_UNDERLINE_RE = re.compile(r"^[_\-—─━\s]{4,}$")

# ── 引注解析正则 ──
_RE_DOI = re.compile(r"(?i)(?:https?://(?:dx\.)?doi\.org/|doi\s*[:：]?\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)")
_RE_YEAR = re.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")
_RE_SIGNAL = re.compile(
    r"^\s*(?:参见|详参|可参见|另见|又见|转引自|详见|对比|同旨可见|例如|如|见)\s*[:：]?\s*"
)
_RE_BACKREF = re.compile(r"^\s*同(?:上|前)注?\s*(?:[〔\[\(（]\s*\d+\s*[〕\]\)）]|第\s*\d+\s*页)?\s*[，,。.]*\s*$")

# 中文标准法学引注：支持「载《刊名》」或直接逗号接《刊名》2023年第4期
_RE_ZH_JOURNAL = re.compile(
    r"(?P<author>[^《\n]{1,60}?)\s*[：:]\s*《(?P<title>[^》\n]{2,180})》\s*"
    r"[，,]?\s*(?:(?:载|刊于)\s*)?《(?P<journal>[^》\n]{2,80})》\s*"
    r"(?P<year>(?:19|20)\d{2})\s*年第\s*(?P<issue>\d{1,3})\s*期"
)
# 文集/集刊析出篇：作者：《篇名》，载张三主编：《书名》，出版社YYYY年版
_RE_ZH_EDITED = re.compile(
    r"(?P<author>[^《\n]{1,60}?)\s*[：:]\s*《(?P<title>[^》\n]{2,180})》\s*"
    r"[，,]?\s*载\s*(?P<editor>[^《\n]{1,40})\s*(?:主编|编)\s*[：:]\s*《(?P<book>[^》\n]{2,120})》"
    r"(?:[^，。\n]*?[，,]\s*(?P<publisher>[^，。\n]{2,40}?(?:出版社|书局|大学))[^0-9\n]*(?P<year>(?:18|19|20)\d{2})\s*年版)?"
)
# 专著 / 译著 / 一般书名（含无「载」但出现出版社+年份或大学博士/硕士论文）
_RE_ZH_BOOK = re.compile(
    r"(?P<author>[^《\n]{1,60}?)\s*[：:]\s*《(?P<title>[^》\n]{2,180})》"
    r"(?P<tail>[^\n]{0,160})"
)
_RE_PUB_YEAR = re.compile(r"(?P<pub>[^\s，,;；]{2,40}?(?:出版社|书局|印书馆|大学))\s*(?P<year>(?:18|19|20)\d{2})\s*年版")
_RE_THESIS = re.compile(r"(?P<univ>[^\s，,;；]{2,40}?大学)\s*(?P<year>(?:19|20)\d{2})\s*年\s*(?:博士|硕士)")

# 明显属于法条/司法解释/官方规范性文件的标题尾缀或制定机关前缀
_STATUTE_TITLE_RE = re.compile(
    r"(?:法|法修正案|条例|细则|规定|办法|解释|批复|决定|通知|通告|公告|规则|准则|章程|纲要|规划|意见|白皮书|公报)$"
)
_ACADEMIC_SUFFIX_RE = re.compile(
    r"(?:研究|论纲|探析|评析|反思|检视|考察|重构|展开|路径|逻辑|机理|困境|进路|边界|适用|解释论|立法论)$"
)
_OFFICIAL_AUTHOR_RE = re.compile(
    r"^(?:全国人大|最高人民法院|最高人民检察院|国务院|司法部|公安部|民政部|教育部|中共中央|国家)"
)


def _source_rev_for(path):
    try:
        p = Path(path)
        st = p.stat()
        with open(p, "rb") as f:
            head = f.read(65536)
        return f"{st.st_size}:{hashlib.sha1(head).hexdigest()[:12]}"
    except Exception:
        return ""


def is_statute_or_official(author, title, has_academic_venue=False):
    """排除法律、法规、司法解释等规范性文件，避免误当作待补充期刊文献。"""
    t = str(title or "").strip()
    a = str(author or "").strip()
    if not t:
        return True
    if _OFFICIAL_AUTHOR_RE.search(a) or "中华人民共和国" in t:
        return True
    if _ACADEMIC_SUFFIX_RE.search(t):
        return False
    if t.startswith("关于") and _STATUTE_TITLE_RE.search(t):
        return True
    if re.search(r"(?:修正案|条例|细则|批复|司法解释|白皮书|通告|公告|公报)$", t):
        return True
    if has_academic_venue and a:
        # 有明确个人学者作者且载于学术期刊/出版社/学位论文，避免误杀《刑事政策与刑法解释》等学术论文
        return False
    if _STATUTE_TITLE_RE.search(t):
        if not a or t.endswith(("法", "条例", "解释", "批复", "白皮书")):
            return True
    return False


def _clean_author(raw):
    s = str(raw or "").strip()
    s = _MARKER_RE.sub("", s).strip()
    s = _SMALL_NUM_MARKER_RE.sub("", s).strip()
    while True:
        nxt = _RE_SIGNAL.sub("", s).strip()
        if nxt == s:
            break
        s = nxt
    # 去掉前缀如「又如」「同样见」「详」
    s = re.sub(r"^(?:又如|详|亦见|参见前引|同注|前注\d+)\s*", "", s).strip()
    # 保留外籍作者国别标记 `[德]`，剥除尾端多余标点
    return s.strip(" \t,，.。:：;；")


def _extract_pdf_lines(pdf_path, max_pages=None):
    """利用 PDFium 提取每页行盒子：[(page_no, page_h, page_w, lines)]。
    lines 为 [(left, bottom, right, top, font_size, text)]，在 extract._PDFIUM_LOCK 下串行访问。"""
    import pypdfium2 as _pdfium
    import pypdfium2.raw as _raw
    import extract as E

    raw_bytes = Path(pdf_path).read_bytes()
    out_pages = []
    with E._PDFIUM_LOCK:
        doc = _pdfium.PdfDocument(raw_bytes)
        try:
            total = len(doc)
            limit = min(total, int(max_pages)) if max_pages else total
            for idx in range(limit):
                pg = doc[idx]
                tp = None
                try:
                    pw, ph = float(pg.get_width()), float(pg.get_height())
                    tp = pg.get_textpage()
                    n_rects = tp.count_rects()
                    boxes = []
                    for r_idx in range(n_rects):
                        l, b, r, t = tp.get_rect(r_idx)
                        txt = (tp.get_text_bounded(l, b, r, t) or "").replace("\r", " ").replace("\n", " ").strip()
                        if not txt:
                            continue
                        fsize = 0.0
                        try:
                            cidx = tp.get_index((l + r) * 0.5, (b + t) * 0.5, 6.0, 6.0)
                            if cidx is not None:
                                fsize = float(_raw.FPDFText_GetFontSize(tp, cidx) or 0.0)
                        except Exception:
                            fsize = 0.0
                        if fsize <= 0:
                            fsize = max(1.0, abs(t - b))
                        boxes.append([float(l), float(b), float(r), float(t), fsize, txt])
                    out_pages.append((idx + 1, ph, pw, _merge_boxes_to_lines(boxes, pw)))
                finally:
                    if tp is not None:
                        try:
                            tp.close()
                        except Exception:
                            pass
                    try:
                        pg.close()
                    except Exception:
                        pass
        finally:
            try:
                doc.close()
            except Exception:
                pass
    return out_pages


def _merge_boxes_to_lines(boxes, page_w):
    """把同一水平基线的文本碎片合并成逻辑行；识别明显双栏，先左栏后右栏。"""
    if not boxes:
        return []
    # 检测中缝空白是否为双栏：主区域行宽度大多小于 0.48 * page_w 且左右均分布
    mid = page_w * 0.5
    left_only = sum(1 for b in boxes if b[2] <= mid + 6)
    right_only = sum(1 for b in boxes if b[0] >= mid - 6)
    spans_mid = sum(1 for b in boxes if b[0] < mid - 20 and b[2] > mid + 20)
    two_col = (left_only >= 6 and right_only >= 6 and spans_mid <= max(2, len(boxes) // 8))

    def _group(col_boxes):
        col_boxes.sort(key=lambda x: (-x[1], x[0]))
        lines = []
        for l, b, r, t, fs, txt in col_boxes:
            if lines and abs(b - lines[-1]["b"]) <= max(3.0, 0.35 * max(fs, lines[-1]["fs"])):
                cur = lines[-1]
                cur["parts"].append((l, txt))
                cur["l"] = min(cur["l"], l)
                cur["b"] = min(cur["b"], b)
                cur["r"] = max(cur["r"], r)
                cur["t"] = max(cur["t"], t)
                cur["fs_list"].append(fs)
            else:
                lines.append({"l": l, "b": b, "r": r, "t": t, "fs": fs, "fs_list": [fs], "parts": [(l, txt)]})
        out = []
        for ln in lines:
            ln["parts"].sort(key=lambda x: x[0])
            text = " ".join(p[1] for p in ln["parts"]).strip()
            text = re.sub(r"\s{2,}", " ", text)
            fs = statistics.median(ln["fs_list"]) if ln["fs_list"] else ln["fs"]
            out.append((ln["l"], ln["b"], ln["r"], ln["t"], fs, text))
        return out

    if not two_col:
        return _group(boxes)
    # 跨越中缝的通栏行放回按 y 归并，双栏各栏独立分组后先左后右（脚注常在底部跨栏）
    upper = [b for b in boxes if b[1] > 0.42 * max(x[3] for x in boxes)]
    lower = [b for b in boxes if b not in upper]
    upper_l = [b for b in upper if (b[0] + b[2]) * 0.5 < mid]
    upper_r = [b for b in upper if (b[0] + b[2]) * 0.5 >= mid]
    return _group(upper_l) + _group(upper_r) + _group(lower)


def _split_page_body_footnotes(page_no, page_h, lines):
    """依据纵向位置、字体相对大小与脚注起始编号，将一页拆为 (body_lines, footnote_items)。"""
    if not lines:
        return [], []
    mid_fs = [ln[4] for ln in lines if 0.25 * page_h <= ln[1] <= 0.85 * page_h and not _UNDERLINE_RE.match(ln[5])]
    body_fs = statistics.median(mid_fs) if mid_fs else statistics.median([ln[4] for ln in lines])

    # 自上而下（y 由大到小）扫描下半页，找第一条命中脚注起点（编号或缩小字体引注线）
    fn_start_idx = None
    for idx, (l, b, r, t, fs, txt) in enumerate(lines):
        if _UNDERLINE_RE.match(txt) and t <= 0.48 * page_h:
            fn_start_idx = idx + 1
            break
        if t > 0.46 * page_h:
            continue
        if _PAGE_FOOTER_RE.match(txt):
            continue
        if _MARKER_RE.match(txt):
            fn_start_idx = idx
            break
        if body_fs > 0 and fs <= body_fs * 0.88 and _SMALL_NUM_MARKER_RE.match(txt):
            fn_start_idx = idx
            break

    if fn_start_idx is None:
        return [ln[5] for ln in lines if not _PAGE_FOOTER_RE.match(ln[5])], []

    body = [ln[5] for ln in lines[:fn_start_idx] if not _UNDERLINE_RE.match(ln[5])]
    raw_fn_lines = lines[fn_start_idx:]
    items = _unwrap_footnote_lines(page_no, raw_fn_lines, page_h, body_fs)
    return body, items


def _unwrap_footnote_lines(page_no, fn_lines, page_h=1000.0, body_fs=11.0):
    """展开页底折行脚注：合并延续行到前一条脚注末尾，直到下一条编号。"""
    items = []
    for l, b, r, t, fs, txt in fn_lines:
        s = str(txt or "").strip()
        if not s or _UNDERLINE_RE.match(s) or _PAGE_FOOTER_RE.match(s):
            continue
        m = _MARKER_RE.match(s) or (
            _SMALL_NUM_MARKER_RE.match(s) if (body_fs > 0 and fs <= body_fs * 0.90) else None
        )
        if m:
            marker = m.group("marker").strip()
            content = s[m.end():].strip()
            items.append({"pdf_page": page_no, "marker": marker, "text": content})
        elif items:
            prev = items[-1]["text"]
            if prev.endswith("-") and re.search(r"[A-Za-z]-$", prev) and re.match(r"^[A-Za-z]", s):
                items[-1]["text"] = prev[:-1] + s
            elif re.search(r"[\u4e00-\u9fff，。；：》）]$", prev) and re.match(r"^[\u4e00-\u9fff《（]", s):
                items[-1]["text"] = prev + s
            else:
                items[-1]["text"] = (prev + " " + s).strip()
        else:
            # 无明确标号但已在分隔线以下（如续页接前页的尾注）
            items.append({"pdf_page": page_no, "marker": "", "text": s})
    return [it for it in items if it.get("text")]


def extract_footnotes_from_page_text(page_no, text):
    """无原始 PDF 文件时的纯文本兜底：按分隔线或行首 ①–㊿ / [N] / 〔N〕 抽取脚注。"""
    lines = [(0.0, 0.0, 0.0, 0.0, 10.0, ln.strip()) for ln in str(text or "").splitlines() if ln.strip()]
    start = None
    for i, ln in enumerate(lines):
        if _UNDERLINE_RE.match(ln[5]):
            start = i + 1
            break
    if start is not None:
        return _unwrap_footnote_lines(page_no, lines[start:])
    # 没有分隔线时，只从末尾连续出现脚注标记的行开始截取
    fn_idx = []
    for i, ln in enumerate(lines):
        if _MARKER_RE.match(ln[5]):
            fn_idx.append(i)
    if not fn_idx:
        return []
    first = fn_idx[0]
    # 确认是从某处开始集中处于页底的脚注段
    if first >= max(0, len(lines) - max(12, len(fn_idx) * 3)):
        return _unwrap_footnote_lines(page_no, lines[first:])
    return [
        {"pdf_page": page_no, "marker": _MARKER_RE.match(lines[i][5]).group("marker"),
         "text": _MARKER_RE.sub("", lines[i][5]).strip()}
        for i in fn_idx
    ]


def split_citation_segments(footnote_text):
    """把一条含多个分号引注的脚注拆成独立引注段（不拆书名号/括号内部的分号）。"""
    s = str(footnote_text or "").strip()
    if not s:
        return []
    segs, buf = [], []
    depth_book, depth_paren = 0, 0
    for ch in s:
        if ch in "《〈":
            depth_book += 1
        elif ch in "》〉" and depth_book > 0:
            depth_book -= 1
        elif ch in "（(":
            depth_paren += 1
        elif ch in "）)" and depth_paren > 0:
            depth_paren -= 1
        if ch in "；;" and depth_book == 0 and depth_paren == 0:
            seg = "".join(buf).strip()
            if seg:
                segs.append(seg)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        segs.append(tail)
    return segs


def _doi_of(text):
    m = _RE_DOI.search(text or "")
    if not m:
        return ""
    d = m.group(1).strip().casefold()
    return d.rstrip(".,;:，；。)]}〉》")


def parse_citation_segment(seg):
    """解析单条中文或英文法学引注；不匹配或属于法规/同前注时返回 None。"""
    s = str(seg or "").strip()
    if not s or _RE_BACKREF.match(s):
        return None
    doi = _doi_of(s)

    # 1. 中文期刊（支持「载《刊名》」与无「载」直连《刊名》）
    m_j = _RE_ZH_JOURNAL.search(s)
    if m_j:
        author = _clean_author(m_j.group("author"))
        title = m_j.group("title").strip()
        journal = m_j.group("journal").strip()
        year = m_j.group("year").strip()
        issue = m_j.group("issue").strip()
        if not is_statute_or_official(author, title, has_academic_venue=True):
            return {
                "kind": "journal",
                "author": author,
                "title": title,
                "journal": journal,
                "year": year,
                "issue": issue,
                "doi": doi,
                "raw": s,
            }
        return None

    # 2. 中文文集/集刊析出篇
    m_ed = _RE_ZH_EDITED.search(s)
    if m_ed:
        author = _clean_author(m_ed.group("author"))
        title = m_ed.group("title").strip()
        book = m_ed.group("book").strip()
        year = (m_ed.group("year") or "").strip()
        if not is_statute_or_official(author, title, has_academic_venue=True):
            return {
                "kind": "bookSection",
                "author": author,
                "title": title,
                "journal": book,
                "year": year,
                "issue": "",
                "doi": doi,
                "raw": s,
            }
        return None

    # 3. 中文专著 / 译著 / 学位论文 / 其他《题名》引注
    m_b = _RE_ZH_BOOK.search(s)
    if m_b:
        author = _clean_author(m_b.group("author"))
        title = m_b.group("title").strip()
        tail = m_b.group("tail") or ""
        m_pub = _RE_PUB_YEAR.search(tail)
        m_th = _RE_THESIS.search(tail)
        if m_pub:
            if not is_statute_or_official(author, title, has_academic_venue=True):
                return {
                    "kind": "book",
                    "author": author,
                    "title": title,
                    "journal": m_pub.group("pub").strip(),
                    "year": m_pub.group("year").strip(),
                    "issue": "",
                    "doi": doi,
                    "raw": s,
                }
            return None
        if m_th:
            if not is_statute_or_official(author, title, has_academic_venue=True):
                return {
                    "kind": "thesis",
                    "author": author,
                    "title": title,
                    "journal": m_th.group("univ").strip(),
                    "year": m_th.group("year").strip(),
                    "issue": "",
                    "doi": doi,
                    "raw": s,
                }
            return None
        if is_statute_or_official(author, title):
            return None
        # 也支持带明显年份与《》的简化期刊/会议引注（如「作者：《题名》，2022年」）
        ym = _RE_YEAR.search(tail) or _RE_YEAR.search(s[m_b.end():])
        if author and ym and len(author) <= 40:
            return {
                "kind": "article",
                "author": author,
                "title": title,
                "journal": "",
                "year": ym.group(1),
                "issue": "",
                "doi": doi,
                "raw": s,
            }

    # 4. 英文学术引注（APA / Chicago / Bluebook）
    if re.search(r"[A-Za-z]{2,}", s):
        ym = _RE_YEAR.search(s)
        if ym:
            year = ym.group(1)
            qm = re.search(r"[\"“](?P<title>[^\"”\n]{4,240})[\"”]", s)
            if qm:
                author = _clean_author(s[:qm.start()])
                tail = s[qm.end():]
                j_raw = re.split(r"\(\d{4}\)|,\s*\d", tail, maxsplit=1)[0]
                journal = re.sub(r"^\s*[,.\s]*|\s*[,.\s]*$", "", j_raw)
                journal = re.sub(r"^\d+\s+", "", journal).strip()
                if author and len(author.split()) <= 18:
                    return {
                        "kind": "journal",
                        "author": author,
                        "title": qm.group("title").rstrip(",.").strip(),
                        "journal": journal,
                        "year": year,
                        "issue": "",
                        "doi": doi,
                        "raw": s,
                    }
            # APA 形态: Author. (2020). Title. Journal...
            m_apa = re.match(r"^(?P<author>[^()]{2,80}?)\s*\(\s*(?P<year>(?:18|19|20)\d{2})\s*\)\.?\s*(?P<rest>.+)$", s)
            if m_apa:
                parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", m_apa.group("rest")) if x.strip()]
                if len(parts) >= 2:
                    journal = re.split(r",\s*\d|\(\d", parts[1], maxsplit=1)[0].strip(" ,.")
                    return {
                        "kind": "journal",
                        "author": _clean_author(m_apa.group("author")),
                        "title": parts[0].rstrip(".!?").strip(),
                        "journal": journal,
                        "year": m_apa.group("year"),
                        "issue": "",
                        "doi": doi,
                        "raw": s,
                    }
    return None


def parse_citations_from_footnotes(footnotes):
    """从页底脚注列表抽出结构化引注列表（附页码 provenance）。"""
    out = []
    for fn in (footnotes or []):
        pg = fn.get("pdf_page")
        marker = fn.get("marker", "")
        text = fn.get("text", "")
        for seg in split_citation_segments(text):
            parsed = parse_citation_segment(seg)
            if not parsed:
                continue
            parsed["pdf_page"] = pg
            parsed["marker"] = marker
            out.append(parsed)
    return out


def mine_paper_footnotes(paper, extracted_pages=None, max_pages=None):
    """懒抽取并缓存单篇文献的页底脚注与引注；不触发向量/全文重索引。"""
    from textutil import safe_name
    key = str((paper or {}).get("key") or "").strip()
    stem = str((paper or {}).get("stem") or (safe_name(key) if key else "")).strip()
    pdf_path = str((paper or {}).get("pdf_path") or (paper or {}).get("fulltext_path") or "").strip()
    fmt = str((paper or {}).get("fulltext_format") or ("pdf" if (paper or {}).get("has_pdf") else "")).lower()

    rev = _source_rev_for(pdf_path) if (pdf_path and fmt == "pdf" and Path(pdf_path).is_file()) else ""
    cache_file = (FOOTNOTES_DIR / f"{stem}.json") if stem else None
    if cache_file and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if (not rev) or cached.get("source_rev") == rev:
                return cached
        except Exception:
            pass

    footnotes = []
    if pdf_path and fmt == "pdf" and Path(pdf_path).is_file():
        try:
            for pg_no, pg_h, _pg_w, lines in _extract_pdf_lines(pdf_path, max_pages=max_pages):
                _, fns = _split_page_body_footnotes(pg_no, pg_h, lines)
                footnotes.extend(fns)
        except Exception:
            footnotes = []

    if not footnotes and extracted_pages:
        for pg in extracted_pages:
            pn = pg.get("page", pg.get("pdf_page"))
            footnotes.extend(extract_footnotes_from_page_text(pn, pg.get("text", "")))

    citations = parse_citations_from_footnotes(footnotes)
    result = {
        "key": key,
        "stem": stem,
        "source_rev": rev,
        "footnotes": footnotes,
        "citations": citations,
    }
    if cache_file and rev:
        try:
            FOOTNOTES_DIR.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, cache_file)
        except Exception:
            pass
    return result
