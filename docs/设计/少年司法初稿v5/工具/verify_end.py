# -*- coding: utf-8 -*-
"""校验「无结论章」不是抽取假象：全文再搜一次结语类标题。"""
import json,os,re,sys,collections
sys.stdout.reconfigure(encoding='utf-8')
H=r"C:\Users\Lsj13\AppData\Local\Temp\claude\D--Onedrive-AI------\56aec02f-aa69-46b1-82fe-883f440b937e\scratchpad"
EX=r"D:\PaperPiggy\data\extracted"
rows=json.load(open(os.path.join(H,"corpus_index.json"),encoding="utf-8"))
CSS={"822QP5P3","W76ZYVVH","VKVBQWSM","J5AQZNCK","3K7BCMHV","492SAXKB","V5UXXRTT","4I7ZNSWZ","FMR4R73F","4RSHIGUL"}
TOP4={"中国社会科学","中国法学","法学研究","中外法学"}
H1=re.compile(r"^([一二三四五六七八九十]{1,3})[、\.．]\s*(.{2,34})$")
NOISE=re.compile(r"页|载《|参见|注释|摘要|关键词|Abstract|作者|收稿|DOI|http")
NUM="一二三四五六七八九十"
END=re.compile(r"结\s*语|结\s*论|余\s*论|结束语|代结")
a=b=c=0; miss=[]
for r in rows:
    if r["journal"].strip() not in TOP4 or r["key"] in CSS or r["nchar"]<8000: continue
    p=os.path.join(EX,r["key"]+".json")
    if not os.path.exists(p): continue
    d=json.load(open(p,encoding="utf-8"))
    full="".join((pg.get("text") or "") for pg in d.get("pages") or [])
    want=0;t1=[]
    for pg in d.get("pages") or []:
        for ln in (pg.get("text") or "").splitlines():
            s=re.sub(r"\s+","",ln.strip())
            if not s or len(s)>40 or NOISE.search(s) or s.startswith("("): continue
            m=H1.match(s)
            if m and want<10 and m.group(1)==NUM[want]:
                t1.append(m.group(2)); want+=1
    if len(t1)<3: continue
    a+=1
    if END.search(t1[-1]): b+=1; continue
    # 抽取判定为「无结论章」：全文（去空白）里再找一次带序号的结语标题
    flat=re.sub(r"\s+","",full)
    if re.search(r"[一二三四五六七八九十][、\.．](结语|结论|余论|结束语|代结语)", flat):
        c+=1; miss.append((r["key"],r["journal"],t1[-1]))
print("总样本 %d 篇" % a)
print("抽取判定「有结语章」：%d 篇" % b)
print("抽取判定「无结论章」：%d 篇，其中全文另找到带序号结语标题的：%d 篇（＝抽取漏计）" % (a-b, c))
print("修正后 真·无独立结论章：%d 篇 = %.0f%%" % (a-b-c, 100*(a-b-c)/a))
print("\n漏计示例（前 10）：")
for k,j,t in miss[:10]: print("  %s %s 末章抽成:%s" % (k,j,t[:22]))
