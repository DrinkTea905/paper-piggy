# -*- coding: utf-8 -*-
"""把三大刊语料分「规范/教义类」与「实证/量化类」，分别统计结构——用户写的是前者。"""
import json,os,re,sys,collections,statistics as st
sys.stdout.reconfigure(encoding='utf-8')
H=r"C:\Users\Lsj13\AppData\Local\Temp\claude\D--Onedrive-AI------\56aec02f-aa69-46b1-82fe-883f440b937e\scratchpad"
EX=r"D:\PaperPiggy\data\extracted"
rows=json.load(open(os.path.join(H,"corpus_index.json"),encoding="utf-8"))
CSS={"822QP5P3","W76ZYVVH","VKVBQWSM","J5AQZNCK","3K7BCMHV","492SAXKB","V5UXXRTT","4I7ZNSWZ","FMR4R73F","4RSHIGUL"}
TOP4={"中国社会科学","中国法学","法学研究","中外法学"}
H1=re.compile(r"^([一二三四五六七八九十]{1,3})[、\.．]\s*(.{2,34})$")
NOISE=re.compile(r"页|载《|参见|注释|摘要|关键词|Abstract|作者|收稿")
NUM="一二三四五六七八九十"
EMP=re.compile(r"实证|数据|问卷|实验|样本|回归|统计|定量|抽样|访谈|变量")
out={"规范教义类":[], "实证量化类":[]}
for r in rows:
    if r["journal"].strip() not in TOP4 or r["key"] in CSS or r["nchar"]<8000: continue
    p=os.path.join(EX,r["key"]+".json")
    if not os.path.exists(p): continue
    d=json.load(open(p,encoding="utf-8")); l1=[]
    for pg in d.get("pages") or []:
        for ln in (pg.get("text") or "").splitlines():
            s=re.sub(r"\s+","",ln.strip())
            if not s or len(s)>40 or NOISE.search(s) or s.startswith("("): continue
            m=H1.match(s)
            if m: l1.append((m.group(1),m.group(2)))
    want=0;t1=[]
    for n,t in l1:
        if want<10 and n==NUM[want]: t1.append(t); want+=1
    if len(t1)<3: continue
    g = "实证量化类" if (EMP.search(r["title"]) or sum(1 for t in t1 if EMP.search(t))>=1) else "规范教义类"
    out[g].append((r,t1))
for g,items in out.items():
    n1=[len(t1) for _,t1 in items]
    print("="*80); print("%s  n=%d 篇" % (g,len(items))); print("="*80)
    print(" 一级单元数 分布:", dict(sorted(collections.Counter(n1).items())), "中位", st.median(n1))
    末=[t1[-1] for _,t1 in items]
    kinds=collections.Counter(["结语类" if re.search(r"结语|结论|余论|结束语",t) else "实质章收尾" for t in 末])
    print(" 末章：", dict(kinds), " → 无独立结论章占 %.0f%%" % (100*kinds["实质章收尾"]/len(末)))
    首=[t1[0] for _,t1 in items]
    print(" 首章叫问题的提出/引言/引论 占 %.0f%%" % (100*sum(1 for t in 首 if re.search(r"问题的提出|引言|引论|导论|绪论",t))/len(首)))
    tl=[len(t) for _,t1 in items for t in t1]
    print(" 一级标题字数 中位 %d，≥15字 %.0f%%" % (st.median(tl),100*sum(1 for x in tl if x>=15)/len(tl)))
    print(" 例：")
    for r,t1 in items[:6]:
        print("   ·", " ／ ".join(t1)[:150])
    print()
