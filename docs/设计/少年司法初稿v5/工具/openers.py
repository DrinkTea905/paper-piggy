# -*- coding: utf-8 -*-
"""抽三大刊规范教义类文章的正文首句（引言起手），看真人怎么开篇。"""
import json,os,re,sys
sys.stdout.reconfigure(encoding='utf-8')
H=r"C:\Users\Lsj13\AppData\Local\Temp\claude\D--Onedrive-AI------\56aec02f-aa69-46b1-82fe-883f440b937e\scratchpad"
EX=r"D:\PaperPiggy\data\extracted"
rows=json.load(open(os.path.join(H,"corpus_index.json"),encoding="utf-8"))
KEYS=["Y2PS87KX","FDSEGY2J","3NT78PBE","XDVR96NW","52QG96AV","CEZQR6FM","GXIRWF87","LIC6NDQD",
      "YBBABVE5","UBBSPRAD","SXMGK5FQ","KEVMVXZT","F284JCJ7","5UGCWUQ2","ZJEWQ3GN","V6UMAERM"]
idx={r["key"]:r for r in rows}
for k in KEYS:
    p=os.path.join(EX,k+".json")
    if not os.path.exists(p): continue
    d=json.load(open(p,encoding="utf-8"))
    t="".join((pg.get("text") or "") for pg in (d.get("pages") or [])[:3])
    t=re.sub(r"\s+","",t)
    m=re.search(r"(问题的提出|引\s*言|引\s*论|一、)",t)
    seg=t[m.end():m.end()+230] if m else t[:230]
    r=idx.get(k,{})
    print("【%s %s %s】%s" % (r.get("journal",""),r.get("year",""),r.get("author","")[:6],r.get("title","")[:30]))
    print("   " + seg + "…\n")
