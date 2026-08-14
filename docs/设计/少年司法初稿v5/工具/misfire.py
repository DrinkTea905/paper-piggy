# -*- coding: utf-8 -*-
"""逐篇回测新版 check_style：106 篇真刊各项误伤率。误伤率 >10% 即阈值错。"""
import importlib.util,io,json,os,re,sys,contextlib
sys.stdout.reconfigure(encoding='utf-8')
H=r"C:\Users\Lsj13\AppData\Local\Temp\claude\D--Onedrive-AI------\56aec02f-aa69-46b1-82fe-883f440b937e\scratchpad"
spec=importlib.util.spec_from_file_location("cs", r"D:\Onedrive\AI\知识库应用\docs\设计\少年司法初稿v4\check_style.py")
cs=importlib.util.module_from_spec(spec); spec.loader.exec_module(cs)
EX=r"D:\PaperPiggy\data\extracted"
rows=json.load(open(os.path.join(H,"corpus_index.json"),encoding="utf-8"))
CSS={"822QP5P3","W76ZYVVH","VKVBQWSM","J5AQZNCK","3K7BCMHV","492SAXKB","V5UXXRTT","4I7ZNSWZ","FMR4R73F","4RSHIGUL"}
TOP4={"中国社会科学","中国法学","法学研究","中外法学"}
CJK=r"\u4e00-\u9fff"
FN=re.compile(r"^\s*(〔\s*\d+\s*〕|\[\s*\d+\s*\]|［\d+］|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\(\s*\d+\s*\))\s*")
CITE=re.compile(r"(参见|载《|译，|出版社|第\s*\d+\s*页|\bpp?\.\s*\d+|University Press|ed\.|Vol\.)")
TRAD=set("為與這們對實現關學說當經濟權灣區證據並眾體處罰調機觀護會來發國")
def clean(pages):
    L=[]
    for txt in pages:
        for ln in txt.splitlines():
            s=ln.strip()
            if not s or len(s)<=3 or re.fullmatch(r"[\-—·•\.\d\s]+",s): continue
            if FN.match(s) or (CITE.search(s) and len(s)<160): continue
            if len(re.findall(r"[A-Za-z]",s))>len(s)*0.4: continue
            L.append(s)
    t="\n".join(L)
    t=re.sub(r"(?<=[%s\u3000-\u303f\uff00-\uffef])[ \t]+(?=[%s\u3000-\u303f\uff00-\uffef])"%(CJK,CJK),"",t)
    return re.sub(r"\n+","",t)
tmp=os.path.join(H,"tmp_doc.txt")
stat={}; low={}; N=0
for r in rows:
    if r["journal"].strip() not in TOP4 or r["key"] in CSS or r["nchar"]<8000: continue
    p=os.path.join(EX,r["key"]+".json")
    if not os.path.exists(p): continue
    d=json.load(open(p,encoding="utf-8"))
    t=clean([(pg.get("text") or "") for pg in d.get("pages") or []])
    hz=re.findall(r"[一-鿿]",t)
    if len(hz)<6000 or sum(1 for c in hz if c in TRAD)/len(hz)>0.012: continue
    open(tmp,"w",encoding="utf-8").write(t); N+=1
    with contextlib.redirect_stdout(io.StringIO()):
        res=cs.run(tmp,as_json=True)
    for x in res["统计型"]:
        if x["判定"]!="OK": stat[x["项"]]=stat.get(x["项"],0)+1
    for x in res["下限"]:
        if x["判定"]!="OK": low[x["项"]]=low.get(x["项"],0)+1
print("逐篇回测 %d 篇真刊\n"%N)
print("§5.1 统计型 误伤：")
for k,v in sorted(stat.items(),key=lambda kv:-kv[1]): print("   %-24s %3d 篇 = %.0f%%"%(k,v,100*v/N))
if not stat: print("   全部 0 ✓")
print("\n§5.4 下限 误伤：")
for k,v in sorted(low.items(),key=lambda kv:-kv[1]): print("   %-24s %3d 篇 = %.0f%%"%(k,v,100*v/N))
if not low: print("   全部 0 ✓")
os.remove(tmp)
