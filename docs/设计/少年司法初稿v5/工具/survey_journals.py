# -*- coding: utf-8 -*-
"""统计库内各期刊篇数与可用全文字数，为「三大刊语料」可行性打底。只读 D:\\PaperPiggy\\data。"""
import json, os, sys, collections
sys.stdout.reconfigure(encoding='utf-8')

EX = r"D:\PaperPiggy\data\extracted"
rows = []
for fn in os.listdir(EX):
    if not fn.endswith(".json"):
        continue
    try:
        d = json.load(open(os.path.join(EX, fn), encoding="utf-8"))
    except Exception:
        continue
    m = d.get("meta") or {}
    pages = d.get("pages") or []
    nchar = sum(len(p.get("text") or "") for p in pages)
    rows.append({
        "key": d.get("key"),
        "journal": (m.get("journal") or "").strip(),
        "title": m.get("title") or "",
        "year": str(m.get("year") or ""),
        "author": m.get("author") or "",
        "tier": m.get("journal_tier") or "",
        "npages": len(pages),
        "nchar": nchar,
    })

by_j = collections.defaultdict(list)
for r in rows:
    by_j[r["journal"]].append(r)

print("总条目：%d，其中有正文的：%d" % (len(rows), sum(1 for r in rows if r["nchar"] > 2000)))
print()
print("== 篇数最多的 40 种期刊 ==")
for j, rs in sorted(by_j.items(), key=lambda kv: -len(kv[1]))[:40]:
    full = [r for r in rs if r["nchar"] > 2000]
    print("%4d 篇 | 有正文 %3d | 总字 %9d | %s" % (len(rs), len(full), sum(r["nchar"] for r in full), j or "(无刊名)"))

print()
TOP = ["中国社会科学", "中国法学", "法学研究", "中外法学", "法学家", "清华法学", "政法论坛",
       "法商研究", "现代法学", "法制与社会发展", "比较法研究", "环球法律评论", "法学评论",
       "法学", "当代法学", "法律科学", "国家检察官学院学报", "中国刑事法杂志", "政治与法律"]
print("== 重点法学期刊逐种 ==")
for t in TOP:
    hit = [r for rs_j, rs in by_j.items() if t in rs_j for r in rs]
    full = [r for r in hit if r["nchar"] > 2000]
    print("%-12s 条目 %3d | 有正文 %3d | 正文总字 %8d" % (t, len(hit), len(full), sum(r["nchar"] for r in full)))

json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus_index.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("\n已写出 corpus_index.json")
