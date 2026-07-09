# -*- coding: utf-8 -*-
"""
步骤2：把提取的逐页文本切成"父子块"。
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

SENT_SPLIT = re.compile(r'(?<=[。！？；!?\n])')

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

def chunk_doc(rec):
    meta = rec["meta"]
    key = rec["key"]
    chunks = []
    for pg in rec.get("pages", []):
        page = pg["page"]
        ptext = pg["text"]
        heading = page_heading(ptext)
        cursor = 0
        for ci, child in enumerate(to_children(ptext)):
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
                "heading": heading,
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
                "itemtype": meta.get("itemtype", ""),
                "has_pdf": True,
                "row_type": "chunk",
            })
    return chunks

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(C.EXTRACTED.glob("*.json"))
    if args.limit: files = files[:args.limit]
    todo = [f for f in files if not (C.CHUNKS / f.name).exists()]
    print(f"[chunk] 提取文件 {len(files)}，待切块 {len(todo)}")
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
        ch = chunk_doc(rec)
        (C.CHUNKS / f.name).write_text(json.dumps(ch, ensure_ascii=False), encoding="utf-8")
        total += len(ch); ndoc += 1
        if i % 100 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}  累计块 {total}  {time.time()-t0:.0f}s")
    print(f"[done] {ndoc} 篇 -> {total} 块"
          + (f"，跳过损坏 {skipped}" if skipped else "") + f"  用时 {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
