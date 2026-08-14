# -*- coding: utf-8 -*-
"""从三大刊语料里抽「论证动作」的真实句式，供新工作流做推荐用语库。"""
import os, re, sys, random, collections
sys.stdout.reconfigure(encoding='utf-8')
random.seed(11)
HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.join(HERE, "corpus")
R = open(os.path.join(C, "R1b_三大刊加中外法学.txt"), encoding="utf-8").read()
SENT = re.compile(r"[^。？！]+[。？！]")
ss = [s.strip() for s in SENT.findall(R) if 12 <= len(s.strip()) <= 130]
print("可用句 %d 条\n" % len(ss))

MOVES = [
    ("① 引入他人观点", r"有学者|论者|有的学者|一种观点|另一种观点|通说|多数说|一般认为|有人主张|学界通常"),
    ("② 表示商榷/否定", r"值得商榷|难以成立|不无疑问|恐难|未必|失之|似有不妥|并不准确|不能成立|存在疑问|有待商榷|误解"),
    ("③ 归谬/推到极端", r"按照这一逻辑|依此逻辑|如果这一|照此|推而广之|势必|将会导致|极端地说"),
    ("④ 让步后转折", r"^(诚然|固然|的确|不可否认|应当承认|无疑)"),
    ("⑤ 举例具体化", r"^(例如|比如|又如|再如|譬如)"),
    ("⑥ 设问推进", r"？$"),
    ("⑦ 下规范判断", r"应当|理应|宜|不宜|有必要"),
    ("⑧ 界定概念", r"所谓|是指|意指|可界定为|在本文中|本文中的"),
    ("⑨ 交代方法/射程", r"本文(不|仅|所|拟|将)|限于篇幅|需要说明|在此不|不作展开"),
    ("⑩ 引条文并解释", r"该条|依该规定|条文的?表述|文义|体系解释|目的解释|立法原意"),
    ("⑪ 比较法引入", r"域外|比较法|德国|日本|美国|英国|我国台湾地区"),
    ("⑫ 段末收束", r"^(可见|由此可见|总之|要之|简言之|归根结底|不难看出|据此)"),
]
for name, pat in MOVES:
    hits = [s for s in ss if re.search(pat, s)]
    print("=" * 88)
    print("%s  —— 语料中 %d 句" % (name, len(hits)))
    print("=" * 88)
    for s in random.sample(hits, min(12, len(hits))):
        print("· " + s)
    print()

# 「有学者」的完整搭配统计
print("=" * 88)
print("「引他人观点」的实际措辞频次（三大刊）")
print("=" * 88)
for pat in ["有学者认为", "有学者指出", "有学者主张", "有的学者", "论者认为", "论者指出",
            "一种观点认为", "另一种观点", "通说认为", "一般认为", "有人认为", "有人主张",
            "学界通常", "多数学者", "亦有学者", "也有学者", "有研究表明", "有研究指出"]:
    print("%-10s %4d" % (pat, len(re.findall(pat, R))))
