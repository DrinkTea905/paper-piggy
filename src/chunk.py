# -*- coding: utf-8 -*-
"""
步骤2：把提取的定位单元文本切成"父子块"。
- 子块(child)：检索单元，~500字、句子边界、轻度重叠。
- 父块(parent)：该块所在"页"的完整文本（≤PARENT_MAX_CHARS），作为给 Claude 的上下文。
- 每块携带可引用元数据：title/author/year/journal/doi/page/heading。
- 断点续跑：每篇输出 data/chunks/<key>.json，已存在则跳过。
用法: python 02_chunk.py [--limit N]
"""
import sys, re, json, argparse, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
from textutil import safe_name

SENT_SPLIT = re.compile(r'(?<=[。！？；!?\n])')

# EN-L1：法条"条"边界。只认**行首**的「第X条」——正文里的交叉引用（如"依照第二百
# 八十条的规定"）出现在句中，若按任意位置切会把一条切得粉碎。法规 PDF 的每条起首
# 基本都独立成行（提取层保留了换行），行首锚定足够稳。
RE_ARTICLE_SPLIT = re.compile(r'(?m)(?=^\s*第[一二三四五六七八九十百千零〇\d]+条(?!例|约|件))')  # 负向前瞻：行首「第三条例外…」不是条界
RE_ARTICLE_NO    = re.compile(r'第[一二三四五六七八九十百千零〇\d]+条')

def sentences(text):
    return [s for s in SENT_SPLIT.split(text) if s.strip()]

def to_children(text):
    """贪心按句聚成 ~CHILD_MAX_CHARS 的子块，相邻块有 overlap。"""
    out, cur = [], ""
    for s in sentences(text):
        # 单句超长：硬切
        while len(s) > C.CHILD_MAX_CHARS:
            head, s = s[:C.CHILD_MAX_CHARS], s[C.CHILD_MAX_CHARS:]
            if cur: out.append(cur); cur = ""
            out.append(head)
        if cur and len(cur) + len(s) > C.CHILD_MAX_CHARS:
            out.append(cur)
            cur = cur[-C.CHILD_OVERLAP_CHARS:] if C.CHILD_OVERLAP_CHARS else ""
        cur += s
    if cur.strip():
        out.append(cur)
    return [c.strip() for c in out if len(c.strip()) >= C.MIN_CHUNK_CHARS]

def page_heading(text):
    for line in text.splitlines():
        ls = line.lstrip()
        if ls.startswith("#"):
            return ls.lstrip("# ").strip()[:80]
    return ""

def _parent_window(ptext, start, child_len):
    """父块以子块位置为中心取 ≤PARENT_MAX_CHARS 的窗口，保证窗口覆盖该子块本身。
    旧实现恒取页首 ptext[:PARENT_MAX_CHARS]，中文法学单页常超 2400 字，页内后半段子块的
    parent 不含自身 → daemon 返回的整页上下文与命中段错位、破坏 grounding。短页(<=上限)时
    窗口即整页，与旧行为一致。"""
    half = max(0, (C.PARENT_MAX_CHARS - child_len) // 2)
    p0 = max(0, start - half)
    p0 = min(p0, max(0, len(ptext) - C.PARENT_MAX_CHARS))
    return ptext[p0: p0 + C.PARENT_MAX_CHARS]

def split_statute_articles(ptext):
    """EN-L1：把一页法条文本按「条」切段，返回 [(条号, 段文本), ...]。
       页首第一处条号之前的部分（章节名/页眉/上一条跨页的尾巴）条号为 ""，
       调用方回退用页级标题。整页找不到行首条号（目录页/附则说明页）→ None，走普通切块。"""
    parts = [p for p in RE_ARTICLE_SPLIT.split(ptext) if p.strip()]
    segs = [(RE_ARTICLE_NO.match(p.lstrip()), p) for p in parts]
    if not any(m for m, _ in segs):
        return None
    return [((m.group(0) if m else ""), p) for m, p in segs]

def _itemtype_map():
    """EN-L1：chunk 阶段拿 itemtype——新提取的 extracted json 的 meta 带 itemtype，
       但历史存量文件可能没有；从 papers.jsonl 建 stem→itemtype 映射兜底。
       papers.jsonl 不存在（如极旧库）→ 空映射，全按普通文献切，行为与旧版一致。"""
    mp = {}
    try:
        if C.PAPERS_JSONL.exists():
            with open(C.PAPERS_JSONL, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        p = json.loads(line)
                        mp[p.get("stem") or safe_name(p.get("key", ""))] = p.get("itemtype", "")
    except Exception:
        return {}
    return mp

def chunk_doc(rec, itemtype_map=None):
    meta = rec["meta"]
    key = rec["key"]
    # EN-L1：itemtype 两级来源：extracted meta 自带 > papers.jsonl 映射兜底（历史存量文件缺该字段）
    itemtype = meta.get("itemtype") or (itemtype_map or {}).get(safe_name(key), "") or ""
    chunks = []
    source_format = rec.get("document_format") or meta.get("fulltext_format") or "pdf"
    for pg in rec.get("pages", []):
        page = pg["page"]
        ptext = pg["text"]
        heading = pg.get("heading") or pg.get("locator_label") or page_heading(ptext)
        # EN-L1：法条先按"条"切段再在条内走既有句聚合；heading 置为条号（引用即指到条）
        segs = split_statute_articles(ptext) if itemtype == "statute" else None
        if segs is None:
            segs = [("", ptext)]
        cursor = 0
        ci = 0
        for seg_heading, seg_text in segs:
            children = to_children(seg_text)
            if not children and seg_heading and seg_text.strip():
                # EN-L1：单条不足最小块长(MIN_CHUNK_CHARS)也**独立成块**，绝不并进下一条——
                # "条"就是法条的引用单位，并块会让「第X条」的引注指到别的条文。
                # 只对带条号的段兜底（seg_heading 非空）：普通文献的超短页（页码/页眉噪声）
                # 维持旧行为产 0 块，不引入噪声块。
                children = [seg_text.strip()]
            for child in children:
                # 前向扫描定位 child 在整页中的起点（用前缀匹配，容忍 strip/overlap 差异）
                core = child[:40]
                idx = ptext.find(core, cursor)
                if idx < 0:
                    idx = ptext.find(core)
                start = idx if idx >= 0 else cursor
                cursor = start + max(1, len(child) - C.CHILD_OVERLAP_CHARS)
                chunks.append({
                    "chunk_id": f"{key}::p{page}::c{ci}",
                    "key": key,
                    "page": page,
                    "heading": seg_heading or heading,
                    "text": child,
                    "parent_text": _parent_window(ptext, start, len(child)),
                    "title": meta.get("title", ""),
                    "author": meta.get("author", ""),
                    "year": meta.get("year", ""),
                    "journal": meta.get("journal", ""),
                    "doi": meta.get("doi", ""),
                    "langid": meta.get("langid", ""),
                    # F 档新列（与 meta 行同 schema，便于统一表检索/去重）
                    "journal_tier": meta.get("journal_tier", ""),
                    "official_pages": meta.get("official_pages", ""),
                    # EN-L1：写解析后的 itemtype（含 papers.jsonl 兜底），statute 行下游
                    # （retriever 的 _weight_res / cite_format 模板分派）都靠它认法条
                    "itemtype": itemtype,
                    "has_pdf": source_format == "pdf",
                    "row_type": "chunk",
                })
                ci += 1
    return chunks

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(C.EXTRACTED.glob("*.json"))
    if args.limit: files = files[:args.limit]
    def _needs(f):
        cf = C.CHUNKS / f.name
        if not cf.exists():
            return True
        try:
            if cf.read_text(encoding="utf-8").strip() not in ("[]", ""):
                return False                    # 已有真实块 → 跳过
            # 空块（当时 extracted ok=False 写的 "[]"）：若现在 extracted 已恢复 ok=True → 重切
            return bool(json.loads(f.read_text(encoding="utf-8")).get("ok"))
        except Exception:
            return False
    todo = [f for f in files if _needs(f)]
    print(f"[chunk] 提取文件 {len(files)}，待切块 {len(todo)}（含恢复重切）")
    imap = _itemtype_map()   # EN-L1：整批建一次 stem→itemtype 映射（历史 extracted 缺 itemtype 时兜底）
    t0 = time.time(); total = 0; ndoc = 0; skipped = 0
    for i, f in enumerate(todo, 1):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            # 空/损坏的 extracted json（如提取时被休眠杀掉留下的半截文件）：跳过不切块，
            # 不写输出，交由 01 重做该篇；避免整批 build 因单个坏文件崩溃。
            print(f"  [skip] {f.name} 读取失败(空/损坏)，跳过: {e}")
            skipped += 1
            continue
        if not rec.get("ok"):
            (C.CHUNKS / f.name).write_text("[]", encoding="utf-8"); continue
        ch = chunk_doc(rec, itemtype_map=imap)
        (C.CHUNKS / f.name).write_text(json.dumps(ch, ensure_ascii=False), encoding="utf-8")
        total += len(ch); ndoc += 1
        if i % 100 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}  累计块 {total}  {time.time()-t0:.0f}s")
    print(f"[done] {ndoc} 篇 -> {total} 块"
          + (f"，跳过损坏 {skipped}" if skipped else "") + f"  用时 {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
