# -*- coding: utf-8 -*-
"""抽出 12 篇三大刊真刊的逐字标题树，合成一份「结构素材」。

只要结构，不要别的——用户 2026-08-14：「基于法学三大刊，学习那些文章的结构（仅仅是结构就好），
就是帮助 ai 搭建好框架，里面它自由发挥」。
"""
import io, sys, os, re, glob, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
V5 = os.path.dirname(HERE)
SRC = os.path.join(V5, "范例库", "_按篇目原始")

items = []
for f in sorted(glob.glob(os.path.join(SRC, "*.md"))):
    t = open(f, encoding="utf-8").read()
    title = re.search(r"^# (.+)$", t, re.M)
    tree = re.search(r"## 逐字标题树\n(.*?)\n## ", t, re.S)
    if not (title and tree):
        continue
    body = tree.group(1).strip()
    body = re.sub(r"^```\w*\n|\n```$", "", body).strip()      # 去代码围栏
    # 一级标题数
    n1 = len(re.findall(r"^[一二三四五六七八九十]+[、\s]", body, re.M))
    has_end = bool(re.search(r"^[一二三四五六七八九十]+[、\s]*(结\s*语|余\s*论|结\s*论)", body, re.M))
    items.append({"title": title.group(1).strip(), "tree": body, "n1": n1, "end": has_end})

out = []
w = out.append
w("# 三大刊的结构：12 篇真实论文的逐字标题树")
w("")
w("> 《中国法学》《法学研究》《中外法学》《中国社会科学》法学篇，从库内 106 篇里挑出的 12 篇，")
w("> 标题逐字照抄（含序号与标点），后面的字数是各单元的实际篇幅。")
w("> **只给结构，不给写法。** 框架搭好之后，里面怎么写你自己定。")
w("")
w("## 先看几个事实（这 12 篇实测）")
w("")
n1s = sorted(x["n1"] for x in items if x["n1"])
ends = sum(1 for x in items if x["end"])
w("- **一级单元数**：%s（中位 %d）。4 到 6 章，没有更多的。" % ("、".join(str(x) for x in n1s), n1s[len(n1s) // 2]))
w("- **结语章：%d 篇有，%d 篇没有——两种都行。** 有的都很短（400—1200 字，王颖那篇「余论」约 500 字）；"
  "没有的就讲完最后一个问题直接收笔，龙宗智那篇的最后一句仍是一个具体的操作主张。" % (ends, len(items) - ends))
w("- **首章：6 篇是「问题的提出」，6 篇直接进实质章**（何挺「研究设计与方法」、"
  "陈光中「逮捕与羁押制度改革的中国实践」、董坤「办案期限抑或羁押期限」）。也是两种都行。")
w("- **一级标题几乎全是名词短语**：12 篇约 60 个一级标题里，只有 1 个不是"
  "（龙宗智「应当谨慎判断『违背立法原意』」）。判断句一般下沉到二级、三级标题。")
w("- **各章篇幅极不均匀，而且这是判断的结果不是缺陷**：郭烁同一章里一节 350 字、另一节 4500 字；"
  "何挺的事实章占全文三分之二、对策章不到六分之一；龙宗智第二章 8300 字、第六章 2500 字。")
w("- **两种值得注意的骨架**：")
w("  - **中心词贯穿、后缀轮换**（汪海燕）：撤回的正当性／对象／理由／证据后果／程序后果——同一个中心词，每章只换后缀。")
w("  - **问题章与对策章逐节对名**（何挺）：适用条件／监督考察／附带条件／撤销 → 适用范围与条件／监督考察／附带条件／配套措施。")
w("")
w("---")
w("")
for x in items:
    w("## %s" % x["title"])
    w("")
    w("```")
    w(x["tree"])
    w("```")
    w("")

dst = os.path.join(V5, "三大刊结构.md")
open(dst, "w", encoding="utf-8", newline="\n").write("\n".join(out))
print("%d 篇 → %s（%d 字符）" % (len(items), dst, len("\n".join(out))))
