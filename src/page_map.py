# -*- coding: utf-8 -*-
"""
研究助手地基（Phase A）：PDF 顺序页 → 期刊印刷页码映射。

中文法学 PDF 的印刷页码稳定出现在每页文本尾部，多为间隔号包裹的数字（·53· / ·145· / 全角·４１９·），
少数为页尾裸数字。本模块检测这些标记，用 official_pages 值域 + 连续性(随PDF页序+1)三重约束互校，
锚定 pdf_page→printed_page，未检出的页按锚点内插/外推；整篇失败则偏移兜底并标 quality=low。
一切引注据此落到"读者翻期刊看到的那一页"，不确定项显式标"（页码推算）"。
sidecar 存 data/pagemap/<stem>.json（只写 DATA，可删可重建，幂等断点续跑）。
"""
import sys, re, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
from textutil import safe_name

# 全角数字 → 半角
_FW = {ord("０") + i: ord("0") + i for i in range(10)}
# 页尾印刷页码：间隔号(·•・‧．. 等)包裹的数字，允许尾随空白/标点
_RE_DOT = re.compile(r"[·•・‧．.\-—]\s*([0-9]{1,4})\s*[·•・‧．.]?\s*$")
# 页尾裸数字（排除紧跟"第"的 pinpoint，如"第 8 页"里的数字不在页尾故一般不误命中）
_RE_BARE = re.compile(r"(?<![0-9第])([0-9]{1,4})\s*$")
# 页眉刊期："《刊名》2023 年第 2 期" / "法学研究 2023 年第 2 期"
_RE_ISSUE = re.compile(r"([0-9]{4})\s*年\s*第\s*([0-9]{1,2})\s*期")


def _norm(s):
    return (s or "").translate(_FW)


def _extracted_path(stem):
    return C.EXTRACTED / f"{stem}.json"


def _sidecar_path(stem):
    return C.PAGEMAP_DIR / f"{stem}.json"


def _detect_printed(text):
    """从一页文本尾部检出印刷页码候选。返回 (int|None, method)。"""
    t = _norm(text or "").rstrip()
    if not t:
        return None, None
    tail = t[-80:]
    m = _RE_DOT.search(tail)
    if m:
        return int(m.group(1)), "detected"
    m = _RE_BARE.search(tail)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 2000:                 # 合理页码范围，滤掉年份等大数
            return n, "detected_bare"
    return None, None


def _detect_issue(text):
    m = _RE_ISSUE.search(_norm(text or "")[:200])   # 页眉在头部
    if m:
        return f"{m.group(1)}年第{m.group(2)}期"
    return ""


def _parse_range(official_pages):
    """'45-67' → (45,67)；单页 '45' → (45,45)；无法解析 → None。"""
    s = _norm(str(official_pages or "")).strip()
    m = re.match(r"^\s*([0-9]{1,5})\s*[-–—~至]\s*([0-9]{1,5})\s*$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    m = re.match(r"^\s*([0-9]{1,5})\s*$", s)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return None


def _longest_monotonic_run(cands, rng):
    """cands: [(pdf_page, printed|None)]（按 pdf_page 升序）。找最长的、printed 随 pdf_page 单调+1、
       且（有值域则）落在 rng 内的运行段。返回该段 [(pdf_page, printed), ...]（可空）。"""
    best = []
    n = len(cands)
    for i in range(n):
        pp0, pr0 = cands[i]
        if pr0 is None:
            continue
        if rng and not (rng[0] <= pr0 <= rng[1]):
            continue
        run = [(pp0, pr0)]
        last_pp, last_pr = pp0, pr0
        for j in range(i + 1, n):
            pp, pr = cands[j]
            if pr is None:
                continue
            # 期望 printed 增量 == pdf_page 增量（一个 PDF 页=一个印刷页）
            if pr - last_pr == pp - last_pp and (not rng or rng[0] <= pr <= rng[1]):
                run.append((pp, pr)); last_pp, last_pr = pp, pr
        if len(run) > len(best):
            best = run
    return best


def build(stem, force=False):
    """为一篇建印刷页码映射并写 sidecar。返回 sidecar dict 或 None（无 extracted）。"""
    stem = safe_name(stem)
    sc = _sidecar_path(stem)
    if sc.exists() and not force:
        try:
            return json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            pass
    ex = _extracted_path(stem)
    if not ex.exists():
        return None
    try:
        d = json.loads(ex.read_text(encoding="utf-8"))
    except Exception:
        return None
    # EPUB/DOCX/Markdown/TXT 没有稳定的 PDF/印刷页码，绝不伪造页码映射。
    if (d.get("document_format") or (d.get("meta") or {}).get("fulltext_format") or "pdf") != "pdf":
        return None
    pages = d.get("pages") or []
    if not pages:
        return None
    official = d.get("official_pages") or (d.get("meta") or {}).get("official_pages") or ""
    rng = _parse_range(official)
    key = d.get("key") or stem

    # 逐页检出候选 + 顺带解析刊期
    cands, issue = [], ""
    for p in pages:
        pp = p.get("page")
        try:
            pp = int(pp)
        except Exception:
            continue
        pr, _ = _detect_printed(p.get("text", ""))
        cands.append((pp, pr))
        if not issue:
            issue = _detect_issue(p.get("text", ""))
    cands.sort(key=lambda x: x[0])

    run = _longest_monotonic_run(cands, rng)
    mp, quality = {}, "low"
    if run and len(run) >= 2:
        # 锚点 + 按步长 1 内插/外推
        anchors = dict(run)
        base_pp, base_pr = run[0]
        for pp, pr in cands:
            if pp in anchors:
                mp[str(pp)] = {"printed": anchors[pp], "method": "detected", "conf": 0.98}
            else:
                mp[str(pp)] = {"printed": base_pr + (pp - base_pp), "method": "interp", "conf": 0.8}
        quality = "high" if rng else "med"
    elif rng:
        # 偏移兜底：第一页=值域起点，逐页+1
        first_pp = cands[0][0] if cands else 1
        for pp, pr in cands:
            mp[str(pp)] = {"printed": rng[0] + (pp - first_pp), "method": "offset", "conf": 0.5}
        quality = "low"
    else:
        # 既无检出运行段又无 official_pages：无法可靠映射，退 PDF 顺序页（标最低）
        for pp, pr in cands:
            mp[str(pp)] = {"printed": pr if pr is not None else pp, "method": "pdfseq", "conf": 0.3}
        quality = "low"

    out = {"stem": stem, "key": key, "official_pages": official,
           "issue": issue, "quality": quality, "map": mp}
    try:
        C.PAGEMAP_DIR.mkdir(parents=True, exist_ok=True)
        sc.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return out


_MEMO = {}

def _load(stem):
    stem = safe_name(stem)
    if stem in _MEMO:
        return _MEMO[stem]
    sc = _sidecar_path(stem)
    doc = None
    if sc.exists():
        try:
            doc = json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            doc = None
    if doc is None:
        doc = build(stem)          # 按需 lazy 建 + 缓存
    if doc is not None:
        _MEMO[stem] = doc          # 只缓存成功结果；None 不入缓存，否则 sidecar 重建后仍被旧空值毒住
    return doc


def printed(key_or_stem, pdf_page):
    """解析某篇某 PDF 页的印刷页码。返回 {printed, method, conf, display}。
       display：高置信直接给数字；推算/低置信标"（页码推算）"，让不确定显式暴露。"""
    doc = _load(key_or_stem)
    try:
        pp = int(pdf_page)
    except Exception:
        pp = None
    if not doc or pp is None:
        return {"printed": None, "method": "none", "conf": 0.0, "display": ""}
    e = (doc.get("map") or {}).get(str(pp))
    if not e:
        return {"printed": None, "method": "none", "conf": 0.0, "display": ""}
    printed_n = e.get("printed")
    method = e.get("method", "")
    conf = e.get("conf", 0.0)
    approx = method in ("interp", "offset", "pdfseq") or conf < 0.7
    disp = "" if printed_n is None else (f"{printed_n}（页码推算）" if approx else str(printed_n))
    return {"printed": printed_n, "method": method, "conf": conf, "display": disp,
            "quality": doc.get("quality"), "issue": doc.get("issue")}


def build_all(force=False):
    """批量为所有 extracted 建映射（随 deep-index 后调用）。返回 (n_built, n_total)。"""
    n = 0
    files = list(C.EXTRACTED.glob("*.json"))
    for f in files:
        if build(f.stem, force=force):
            n += 1
    return n, len(files)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if a.all:
        print(page_map_all := build_all(force=a.force))
    elif a.stem:
        print(json.dumps(build(a.stem, force=a.force), ensure_ascii=False, indent=1))
