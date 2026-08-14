# -*- coding: utf-8 -*-
"""从范例库生成索引（进 git）。原文不进 git，索引含 key＋标签＋印刷页＋前16字＋字数＋sha16，
据此可校验范例库是否被改动、也可回源定位到原刊。"""
import io, sys, os, re, glob, json, hashlib, collections, unicodedata
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
V5 = os.path.dirname(HERE)
LIB = os.path.join(V5, "范例库")
frozen = json.load(open(os.path.join(V5, "语料", "冻结清单.json"), encoding="utf-8"))
desc = {k: v["desc"] for k, v in frozen.get("read12_hash", {}).items()}

SEC = re.compile(r"^### 【([^】]+)】\s*(.*)$", re.M)
rows, per_tag = [], collections.Counter()
for f in sorted(glob.glob(os.path.join(LIB, "*.md"))):
    key = os.path.splitext(os.path.basename(f))[0]
    txt = open(f, encoding="utf-8").read()
    parts = SEC.split(txt)
    for i in range(1, len(parts) - 2, 3):
        tag, loc, body = parts[i], parts[i + 1].strip(), parts[i + 2]
        body = re.split(r"^—\s*为什么值得当范例", body, maxsplit=1, flags=re.M)[0].strip()
        han = re.findall(r"[\u4e00-\u9fff]", body)
        rows.append({
            "key": key, "tag": tag, "loc": loc,
            "head": "".join(han[:16]),
            "chars": len(han),
            "sha16": hashlib.sha256(unicodedata.normalize("NFKC", body).encode("utf-8")).hexdigest()[:16],
        })
        per_tag[tag] += 1

out = []
w = out.append
w("# 范例库索引（阶段 2）")
w("")
w("> **范例库正文不进 git**（第三方版权，`.gitignore` 已挡）。本索引进 git，作用有三：")
w("> ① 记录每段范例的出处与定位，可回源到原刊；② 存 sha16，可校验范例库有没有被改动；")
w("> ③ 让阶段 3 的规则提炼能引用到具体某一段，而不必把原文抄进台账。")
w("")
w("生成：`工具/gen_lib_index.py`　｜　真伪校验：`工具/verify_excerpts.py`（**134 段全部回原文命中，零编造**）")
w("")
w("## 规模")
w("")
w("| 项 | 数 |")
w("|---|---:|")
w("| 精读篇目 | %d |" % len({r["key"] for r in rows}))
w("| 成段摘录 | %d 段 |" % len(rows))
w("| 摘录总量 | %d 汉字 |" % sum(r["chars"] for r in rows))
w("| 单段长度 | %d—%d 汉字（中位 %d） |" % (
    min(r["chars"] for r in rows), max(r["chars"] for r in rows),
    sorted(r["chars"] for r in rows)[len(rows) // 2]))
w("")
w("## 动作标签分布")
w("")
w("| 标签 | 段数 | 治哪条病症 |")
w("|---|---:|---|")
CURE = {"下判断": "S1 通篇自我设限（★用户点名最严重）", "真交锋": "S2 论敌一律被收编",
        "实景": "S7 零个中国个案", "数据推论": "S6 材料只当背景板", "命名": "M1 论敌全匿名",
        "段落推进": "S5 所有段落一个模子", "比较法": "S8 比较法只做展柜陈列",
        "立法建议": "S9 拟完条文就转场", "让步": "S1 让步之后主命题变弱"}
for t, n in per_tag.most_common():
    w("| %s | %d | %s |" % (t, n, CURE.get(t, "—")))
w("")
w("## 逐段索引")
w("")
w("| key | 标签 | 位置 | 前 16 字 | 汉字 | sha16 |")
w("|---|---|---|---|---:|---|")
for r in rows:
    w("| `%s` | %s | %s | %s… | %d | `%s` |" % (r["key"], r["tag"], r["loc"], r["head"], r["chars"], r["sha16"]))
w("")
w("## 篇目一览")
w("")
for k in sorted({r["key"] for r in rows}):
    w("- `%s` %s —— %d 段" % (k, desc.get(k, ""), sum(1 for r in rows if r["key"] == k)))
w("")

dst = os.path.join(V5, "台账", "02_范例库索引.md")
open(dst, "w", encoding="utf-8", newline="\n").write("\n".join(out))
json.dump(rows, open(os.path.join(V5, "台账", "02_范例库索引.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("%d 段 / %d 篇 / %d 汉字 → %s" % (len(rows), len({r['key'] for r in rows}),
                                        sum(r["chars"] for r in rows), dst))
