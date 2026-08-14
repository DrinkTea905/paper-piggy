# -*- coding: utf-8 -*-
"""阶段1：冻结语料清单。

产出 `语料/冻结清单.md`（进 git）与 `语料/冻结清单.json`（进 git）。
语料正文本身不进 git（第三方版权），可回源靠「本脚本 ＋ 清单里的 sha256」：
重建后逐篇比对 sha256，一致即证明是同一批语料。

分层（v4 的口径混乱在此定死）：
  L1  文体基准 = 用户口径三大刊（中国法学／法学研究／中外法学）＋《中国社会科学》法学篇
      ⚠️ v4 脚本里的 TOP3 是「社科＋中法＋法研」，把《中外法学》排除在外，与用户定义不同。
      本轮一律以 L1 为准，v4 那个口径作废。
  L2a 领域惯例·法学核心刊   少年司法题材，发在 CLSCI/CSSCI 法学刊上
  L2b 领域惯例·专门刊       少年司法题材，发在青少年/青年类专门刊上
  L2c 线索层                港台、外文、学报、普刊、无刊名 —— 只作检索线索，不用于提炼规则
"""
import json, io, sys, os, re, hashlib, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
V5 = os.path.dirname(HERE)
EX = r"D:\PaperPiggy\data\extracted"
OUT = os.path.join(V5, "语料")
os.makedirs(OUT, exist_ok=True)

rows = json.load(open(os.path.join(HERE, "corpus_index.json"), encoding="utf-8"))

# 《中国社会科学》非法学篇（v4 已人工判定，本轮沿用并复核）
CSS_NONLAW = {"822QP5P3", "W76ZYVVH", "VKVBQWSM", "J5AQZNCK", "3K7BCMHV",
              "492SAXKB", "V5UXXRTT", "4I7ZNSWZ", "FMR4R73F", "4RSHIGUL"}
TOP = {"中国法学", "法学研究", "中外法学", "中国社会科学"}
JJ = re.compile(r"未成年|少年|低龄|罪错|附条件不起诉|专门矫治|专门教育|收容教养|监护|儿童|校园|欺凌|触法|工读")

CORE_LAW = {"中国刑事法杂志", "政法论坛", "比较法研究", "环球法律评论", "法学评论", "清华法学",
            "法学家", "法制与社会发展", "现代法学", "法律科学", "法商研究", "国家检察官学院学报",
            "政治与法律", "法学", "法学杂志", "中国法律评论", "法律适用", "法学论坛", "河北法学",
            "东方法学", "行政法学研究", "当代法学", "交大法学", "南大法学", "华东政法大学学报"}
SPECIAL = {"青少年犯罪问题", "预防青少年犯罪研究", "中国青年社会科学", "中国青年研究", "青年研究"}

# 阶段 2 精读篇目（12 篇）。跨作者门槛：同一作者最多 2 篇。
READ12 = {
    # ── 少年司法题材（L1 内仅有的 5 篇，全部精读）──
    "GXIRWF87": "王颖·少年刑事司法基本原则之重构·中国法学2026｜理论重构＋原则三连",
    "LIC6NDQD": "姚建龙·少年司法社会调查程序·法学研究2026｜正本清源—功能辨析—立法优化",
    "UBBSPRAD": "叶小琴·未成年人保护立法的理念与制度体系·中外法学2022｜体系型",
    "I94VN74H": "何挺·附条件不起诉实施状况·法学研究2019｜实证型（问题章与对策章逐节对名）",
    "YBBABVE5": "姚建龙·未成年人违警行为·中国法学2022｜立法辨证",
    # ── 刑诉程序类（v4 精读过 4 篇，本轮复核复用）──
    "Y2PS87KX": "董坤·刑事诉讼办案期限·法学研究2024｜★实景（K市中院电话）＋解释链＋数据",
    "FDSEGY2J": "汪海燕·被追诉人认罪认罚的撤回·法学研究2020｜★命名法（给每一说起名）",
    "3NT78PBE": "龙宗智·立法原意何处寻·中国法学2021｜★下重话（显属语病／令人遗憾）",
    "CEZQR6FM": "郭烁·羁押人口率·中外法学2024｜★改造度量衡＋数据推论",
    # ── 刑诉程序类（v4 未读，本轮新增，防止再从同一批样本归纳）──
    "SZN5GJA9": "亢晶晶·职权主导型刑事分案模式·中外法学2022｜表现—机理—风险—优化",
    "IRCX4454": "李倩·刑事速裁程序评判 以德国刑事处罚令为参照·中外法学2020｜★比较法怎么用",
    "SXMGK5FQ": "陈光中、路旸·逮捕与羁押制度改革·中国法学2023｜★对策章怎么写",
}


def load_text(key):
    p = os.path.join(EX, key + ".json")
    if not os.path.exists(p):
        return ""
    d = json.load(open(p, encoding="utf-8"))
    return "".join((pg.get("text") or "") for pg in d.get("pages") or [])


def sha(t):
    return hashlib.sha256(t.encode("utf-8", "ignore")).hexdigest()[:16]


def han(t):
    return len(re.findall(r"[\u4e00-\u9fff]", t))


L1, L2a, L2b, L2c = [], [], [], []
for r in rows:
    j = (r["journal"] or "").strip()
    if r["nchar"] <= 5000:
        continue
    if j in TOP and r["key"] not in CSS_NONLAW:
        L1.append(r)
    elif JJ.search(r["title"] or ""):
        (L2a if j in CORE_LAW else L2b if j in SPECIAL else L2c).append(r)

manifest = {"layers": {}, "read12": READ12}
lines = []
w = lines.append
w("# 语料冻结清单（阶段 1）")
w("")
w("> 冻结日期：2026-08-14。**语料正文不进 git**（第三方版权），本清单进 git。")
w("> 可回源：`工具/survey_journals.py` → `工具/build_corpus2.py` 重建后，逐篇比对本表 sha256，一致即同一批语料。")
w("> sha256 取**未清洗的原始提取正文**（`D:\\PaperPiggy\\data\\extracted\\<key>.json` 各页 text 顺序拼接）前 16 位。")
w("")
w("## 口径（v4 的混乱在此定死）")
w("")
w("| 层 | 定义 | 篇数 | 用途 |")
w("|---|---|---:|---|")
for name, arr, use in [
    ("**L1 文体基准**", L1, "学文体与结构。＝用户口径三大刊＋《中国社会科学》法学篇"),
    ("L2a 领域·法学核心刊", L2a, "学少年司法的领域惯例（法学话语）"),
    ("L2b 领域·专门刊", L2b, "学领域惯例（青少年研究话语，注意刊物层级低于 L2a）"),
    ("L2c 线索层", L2c, "**不用于提炼规则**，只作检索线索（港台／外文／学报／无刊名）"),
]:
    w("| %s | %s | %d | %s" % (name, "—", len(arr), use) + " |")
w("")
w("⚠️ **v4 的 `TOP3` 是「中国社会科学＋中国法学＋法学研究」，把《中外法学》排除在外**，")
w("与用户对「三大刊」的定义不同；v4 的行文阈值用 106 篇（含中外法学）而结构基准用 86 篇（能自动抽标题树者），")
w("两套数字分母不同。本轮一律以 L1 = %d 篇为准，v4 的两个口径同时作废。" % len(L1))
w("")

w("## 跨作者门槛（v2 最扎实的一条，v3 丢掉了，本轮恢复）")
w("")
w("> **一条规则要进工作流，须得到 ≥3 篇、≥2 个作者来源的支持。**")
w("> 阶段 2 的精读篇目本身也守这条：同一作者最多 2 篇。")
w("")
au = collections.Counter()
for k in READ12:
    r = next((x for x in rows if x["key"] == k), None)
    if r:
        au[(r["author"] or "").split(";")[0].strip()] += 1
w("精读 12 篇的作者分布：%s（最多 %d 篇，达标）"
  % ("、".join("%s×%d" % (a, c) for a, c in au.most_common()), max(au.values()) if au else 0))
w("")

w("## 阶段 2 精读篇目（12 篇）")
w("")
w("| key | 篇目与看点 | 层 | sha256 | 汉字 |")
w("|---|---|---|---|---:|")
for k, desc in READ12.items():
    t = load_text(k)
    lay = "L1" if any(x["key"] == k for x in L1) else "?"
    w("| `%s` | %s | %s | `%s` | %d |" % (k, desc, lay, sha(t), han(t)))
    manifest.setdefault("read12_hash", {})[k] = {"sha16": sha(t), "han": han(t), "desc": desc}
w("")
w("**选取理由**：① L1 里全部 5 篇少年司法题材悉数纳入（题材＋文体双命中，最宝贵）；")
w("② 刑诉程序类 7 篇，其中 4 篇是 v4 精读过的（复核复用，不是照抄结论），")
w("3 篇是 v4 从未读过的新样本——**防止再从同一批样本归纳出「技艺」**（v4 的 n=1 教训）；")
w("③ 覆盖阶段 0 病症清单要治的正面动作：实景（董坤）、命名法（汪海燕）、下重话（龙宗智）、")
w("数据推论（郭烁）、比较法（李倩）、对策章（陈光中）、实证结构（何挺）。")
w("")
w("⚠️ **与阶段 5 验证题的隔离**：精读 12 篇中无一篇与验证题 A（未成年被害人参与）、")
w("B（专门矫治教育决定程序）同题。姚莉《轻微犯罪记录封存》与姚建龙《社会调查程序》")
w("虽在库内，但因**与验证题同题会污染盲读**，验证题已排除这两个方向。")
w("")

for name, arr in [("L1 文体基准", L1), ("L2a 领域·法学核心刊", L2a), ("L2b 领域·专门刊", L2b)]:
    w("## %s（%d 篇）" % (name, len(arr)))
    w("")
    w("| key | 刊 | 年 | 作者 | 题名 | sha256 | 汉字 |")
    w("|---|---|---|---|---|---|---:|")
    recs = []
    for r in sorted(arr, key=lambda x: ((x["journal"] or ""), -(int(x["year"][:4]) if (x["year"] or "")[:4].isdigit() else 0))):
        t = load_text(r["key"])
        h, n = sha(t), han(t)
        w("| `%s` | %s | %s | %s | %s | `%s` | %d |"
          % (r["key"], (r["journal"] or "")[:8], (r["year"] or "")[:4],
             (r["author"] or "")[:14], (r["title"] or "")[:44], h, n))
        recs.append({"key": r["key"], "journal": r["journal"], "year": r["year"],
                     "author": r["author"], "title": r["title"], "sha16": h, "han": n})
    manifest["layers"][name] = recs
    w("")

w("## L2c 线索层（%d 篇，不用于提炼规则）" % len(L2c))
w("")
w("只列刊物分布，逐篇清单见 `冻结清单.json`：")
w("")
c = collections.Counter((r["journal"] or "(无刊名)").strip() for r in L2c)
w("　" + "；".join("%s %d" % (k[:16], v) for k, v in c.most_common(18)))
w("")
manifest["layers"]["L2c 线索层"] = [
    {"key": r["key"], "journal": r["journal"], "year": r["year"],
     "author": r["author"], "title": r["title"]} for r in L2c]

open(os.path.join(OUT, "冻结清单.md"), "w", encoding="utf-8", newline="\n").write("\n".join(lines))
json.dump(manifest, open(os.path.join(OUT, "冻结清单.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("L1=%d  L2a=%d  L2b=%d  L2c=%d  精读=%d" % (len(L1), len(L2a), len(L2b), len(L2c), len(READ12)))
print("作者分布：", dict(au))
print("已写出", os.path.join(OUT, "冻结清单.md"))
