# -*- coding: utf-8 -*-
"""全语料级：标题密度、分节率、末章形态。避免用 6 篇小样本下结论。"""
import json,os,re,sys,collections,statistics as st
sys.stdout.reconfigure(encoding='utf-8')
H=r"C:\Users\Lsj13\AppData\Local\Temp\claude\D--Onedrive-AI------\56aec02f-aa69-46b1-82fe-883f440b937e\scratchpad"
EX=r"D:\PaperPiggy\data\extracted"
rows=json.load(open(os.path.join(H,"corpus_index.json"),encoding="utf-8"))
CSS={"822QP5P3","W76ZYVVH","VKVBQWSM","J5AQZNCK","3K7BCMHV","492SAXKB","V5UXXRTT","4I7ZNSWZ","FMR4R73F","4RSHIGUL"}
TOP4={"中国社会科学","中国法学","法学研究","中外法学"}
H1=re.compile(r"^([一二三四五六七八九十]{1,3})[、\.．]\s*(.{2,34})$")
H2=re.compile(r"^[（(]([一二三四五六七八九十]{1,3})[）)]\s*(.{2,34})$")
H3=re.compile(r"^(\d{1,2})[\.．、]\s*(.{2,30})$")
NOISE=re.compile(r"页|载《|参见|注释|摘要|关键词|Abstract|作者|收稿|DOI|http")
NUM="一二三四五六七八九十"
recs=[]
for r in rows:
    if r["journal"].strip() not in TOP4 or r["key"] in CSS or r["nchar"]<8000: continue
    p=os.path.join(EX,r["key"]+".json")
    if not os.path.exists(p): continue
    d=json.load(open(p,encoding="utf-8"))
    l1=[];n2=0;n3=0
    for pg in d.get("pages") or []:
        for ln in (pg.get("text") or "").splitlines():
            s=re.sub(r"\s+","",ln.strip())
            if not s or len(s)>40 or NOISE.search(s): continue
            if H2.match(s): n2+=1; continue
            if s.startswith("("): continue
            m=H1.match(s)
            if m: l1.append(m.group(2)); continue
            if H3.match(s) and len(s)<26: n3+=1
    want=0;t1=[]
    for i,t in enumerate(l1):
        pass
    # 重新按序号过滤
    l1seq=[]
    want=0
    for pg in d.get("pages") or []:
        for ln in (pg.get("text") or "").splitlines():
            s=re.sub(r"\s+","",ln.strip())
            if not s or len(s)>40 or NOISE.search(s) or s.startswith("("): continue
            m=H1.match(s)
            if m and want<10 and m.group(1)==NUM[want]:
                l1seq.append(m.group(2)); want+=1
    if len(l1seq)<3: continue
    body=r["nchar"]
    recs.append(dict(key=r["key"],j=r["journal"],n1=len(l1seq),n2=n2,n3=n3,nchar=body,t1=l1seq))
print("样本 %d 篇\n" % len(recs))
dens=[r["nchar"]/(r["n1"]+r["n2"]+r["n3"]) for r in recs if (r["n1"]+r["n2"]+r["n3"])>0]
print("标题密度（字/标题）：中位 %.0f  均值 %.0f  四分位 %.0f—%.0f  ≥1400 的篇数 %d/%d = %.0f%%" %
      (st.median(dens),st.mean(dens),sorted(dens)[len(dens)//4],sorted(dens)[3*len(dens)//4],
       sum(1 for x in dens if x>=1400),len(dens),100*sum(1 for x in dens if x>=1400)/len(dens)))
r2=[r["n2"]/r["n1"] for r in recs]
print("平均每章二级标题数：中位 %.1f" % st.median(r2))
n3u=sum(1 for r in recs if r["n3"]>=3)
print("用到三级标题（≥3 个）的篇数：%d/%d = %.0f%%" % (n3u,len(recs),100*n3u/len(recs)))
print("完全不用三级的：%d/%d = %.0f%%" % (sum(1 for r in recs if r["n3"]<3),len(recs),100*sum(1 for r in recs if r["n3"]<3)/len(recs)))
末=collections.Counter()
for r in recs:
    t=r["t1"][-1]
    末["结语/结论/余论" if re.search(r"结语|结论|余论|结束语",t) else "无结论章（末章即实质章）"]+=1
print("\n末章：",dict(末))
首=collections.Counter("问题的提出/引言型" if re.search(r"问题的提出|引言|引论|导论|绪论|问题与",r["t1"][0]) else "实质章" for r in recs)
print("首章：",dict(首))
print("\n一级单元数分布：",dict(sorted(collections.Counter(r["n1"] for r in recs).items())))
