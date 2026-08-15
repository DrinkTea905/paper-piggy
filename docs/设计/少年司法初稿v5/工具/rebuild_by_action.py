# -*- coding: utf-8 -*-
"""把范例库从「按篇目」重排成「按动作」。

v5 判废的直接原因：产物又变成了一份规则文档。诊断见 v5失败复盘.md §五——
材料没错（134 段范例、7 项禁区、12 篇语料、一条工序都在），错的是封装：
  ① 范例库按篇目组织，写批判章要读「下判断」得翻 12 个文件，先读后写没法执行；
  ② 14 条规则被写成独立一章，规则一旦成章，文档就变回 v4。

本脚本重排为：一个动作一个文件，规则拆开附在各自动作的开头（当作「读的时候看什么／
写完问自己什么」），不再单独成册。

判据（每次改完自测）：**「关于怎么写的说明文字」总量必须远少于「真刊原文」总量。**
"""
import io, sys, os, re, glob, json, shutil, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
V5 = os.path.dirname(HERE)
LIB = os.path.join(V5, "范例库")
SRC = os.path.join(LIB, "_按篇目原始")

# 规则拆开挂到各动作下。一句话版，不带边界说明（边界留在台账 03_规则与反例.md）。
ACTIONS = {
    "下判断": ("读的时候看：真人在下重话之前，垫了多长的分析？重话的落点收得多窄？",
             ["写完问自己：把这段的材料换成另一组同类材料，判断会不会变？不变就是回填的。〔R1〕",
              "写完问自己：替对方数一数，他能从几个方向反驳你？多于一个，就说明你判的比你证的大。〔R2〕",
              "写完问自己：这个判断，读者能拿什么去核对？说不出案号／口径／条文号／某处删改，它就还只是推想。〔R12〕"]),
    "真交锋": ("读的时候看：他抽掉的是对方推理的哪一环？他是在否证，还是在限缩射程？",
             ["写完问自己：我做的是否证（抽掉这一环对方结论就塌）还是限缩（他理论上对、但在这里不适用）？"
              "两件都可以做，**不可以把限缩伪装成否证**。〔R4〕"]),
    "让步": ("读的时候看：他让掉了什么？让完之后，他的主张是被磨准了，还是被削小了？",
            ["写完问自己：把让步前和让步后各自敢主张的最强命题逐字写下来，"
             "第二句只要比第一句小或软，这次让步就是失败的。〔R3〕",
             "分清两种句：射程声明（这一问题不在本文射程之内）留；免责句（本文并不主张／须作两点限缩）删。"]),
    "实景": ("读的时候看：他给的是哪一年、哪个机关、谁在做什么动作？抽象判断是在实景之后才落的吗？",
            ["写完问自己：这一章有没有一个可指认的具体物？自造的假想案例不算。〔R12〕",
             "写完问自己：关于我自己的那句话（查了什么／没查到什么），删掉它读者还能不能核对结论、"
             "还知不知道该在多大范围内相信它？不能就留，只证明我查过了就移进检索日志。〔R9〕"]),
    "数据推论": ("读的时候看：这组数字推出了什么判断？换一组数字，结论会不会变？",
              ["写完问自己：删掉这组材料，这个判断会不会被另一条解释路径接管？"
               "能被接管、而我的方案正建立在排除那条路径之上，它才是承重的。〔R10〕",
               "写完问自己：这一章有没有哪一处，是材料让我改了原来的想法（哪怕对我不利）？"
               "一处都没有，说明结论在读材料之前就定型了。〔R11〕"]),
    "命名": ("读的时候看：这个名字是怎么被证明出来的（先复述原话？先归拢条文？先演示两个口径的差别）？"
           "起名之后，它在后文当把手用了吗？",
           ["写完问自己：凡在文中会被提到两次以上的观点，我给它名字了吗？名字是被证明出来的还是贴上去的？〔R16〕"]),
    "比较法": ("读的时候看：这段外国法是在说「别人也有」，还是「别人有的是另一种／试过失败了／自己承认做不到」？",
            ["写完问自己：删掉这几段，本文哪个结论会塌、哪条方案会缺一个零件？答不上来就整段删。〔R13〕"]),
    "立法建议": ("读的时候看：他有没有交代这条建议会怎样失灵、边界在哪、会压坏什么？",
             ["写完问自己（两问至少答出一个）：① 被规制者最省事的规避动作是什么？"
              "② 它会压坏哪一个既有价值、我打算把它收窄到什么程度？〔R14〕"]),
    "段落推进": ("读的时候看：这一段是被什么拽着往前走的？它的最后一句在干什么——收束，还是开新战场？",
             ["写完问自己：这段最后一句删掉，读者损失什么？什么也不损失就删。〔R6〕",
              "每章封笔问自己：用一个动词写出每段怎么往前走（加码／推到最坏／因果链／连问／"
              "让原话收尾／用材料反证自己）。写不出相邻几段的差别，就是同一台发动机。〔R7〕",
              "每章封笔问自己：写成第一第二第三的那几层，重量真的相等吗？"
              "轻的一句带过，甚至直说这一点不必多说。〔R8〕"]),
}

frozen = json.load(open(os.path.join(V5, "语料", "冻结清单.json"), encoding="utf-8"))
DESC = {k: v["desc"].split("｜")[0] for k, v in frozen.get("read12_hash", {}).items()}

# ① 把原按篇目的 12 个文件挪进子目录，保留可回源
os.makedirs(SRC, exist_ok=True)
for f in glob.glob(os.path.join(LIB, "*.md")):
    if os.path.basename(f) in ("怎么用.md",) or os.path.basename(f).startswith("_"):
        continue
    if re.fullmatch(r"[A-Z0-9]{8}\.md", os.path.basename(f)):
        shutil.move(f, os.path.join(SRC, os.path.basename(f)))

# ② 解析全部段落
SEC = re.compile(r"^### 【([^】]+)】\s*(.*)$", re.M)
by_tag = collections.defaultdict(list)
for f in sorted(glob.glob(os.path.join(SRC, "*.md"))):
    key = os.path.splitext(os.path.basename(f))[0]
    parts = SEC.split(open(f, encoding="utf-8").read())
    for i in range(1, len(parts) - 2, 3):
        tag, loc, rest = parts[i], parts[i + 1].strip(), parts[i + 2]
        m = re.split(r"^—\s*为什么值得当范例[：:]\s*", rest, maxsplit=1, flags=re.M)
        body = m[0].strip()
        why = (m[1].strip().split("\n")[0] if len(m) > 1 else "")
        by_tag[tag].append({"key": key, "loc": loc, "body": body, "why": why})

# ③ 一个动作一个文件
han = lambda t: len(re.findall(r"[一-鿿]", t))
tot_src = tot_note = 0
for tag, items in by_tag.items():
    look, asks = ACTIONS.get(tag, ("", []))
    out = ["# %s（%d 段真刊原文）" % (tag, len(items)), ""]
    if look:
        out += ["> %s" % look, ""]
    if asks:
        out += ["**写完问自己：**", ""] + ["- %s" % a.replace("写完问自己：", "").replace("每章封笔问自己：", "（每章封笔）") for a in asks] + [""]
    out += ["---", ""]
    for it in items:
        out += ["### %s · %s" % (DESC.get(it["key"], it["key"]), it["loc"]), "",
                it["body"], ""]
        if it["why"]:
            out += ["*%s*" % it["why"], ""]
        tot_src += han(it["body"])
        tot_note += han(it["why"])
    p = os.path.join(LIB, "%s.md" % tag)
    open(p, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    tot_note += han(look) + sum(han(a) for a in asks)
    print("%-6s %2d 段 → %s" % (tag, len(items), os.path.basename(p)))

print("\n真刊原文 %d 汉字 ｜ 说明文字 %d 汉字 ｜ 比 %.1f : 1"
      % (tot_src, tot_note, tot_src / max(tot_note, 1)))
print("（判据：说明文字必须远少于真刊原文。v5 草案的比例是反的，那正是它变回 v4 的机械判据。）")
