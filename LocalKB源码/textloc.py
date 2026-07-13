# -*- coding: utf-8 -*-
"""
EN-A3：引文定位器（契约6）——在 data/extracted/{stem}.json 的逐页文本里定位一段引文，
回答「这句话在哪篇、PDF 第几页、印刷页第几页」。

为什么要有它：引注核验（蓝图 G1/G2）的最后一步永远是"回到原文那一页"——检索只能给
相关块，给不出可供脚注引用的精确页位；agent 替用户核对引文时必须有这个原子能力。

匹配策略（刻意不上编辑距离库——归一化后子串匹配已覆盖 PDF 抽取文本的主要噪声源）：
  - exact：去空白 + 全角→半角 + 引号剥除后做子串匹配（PDF 抽取最常见的差异就是
    空白被打散、全半角混用、引号被换样式）；
  - fuzzy：在 exact 基础上再剥掉全部常见中英标点（容忍「，」vs「,」、顿号/逗号互换、
    书名号有无等纯标点差异；文字有一字之差就不算命中——那正是需要人工复核的信号）。
归一化时同步维护"归一化位置→原文位置"的映射，命中后能取回原文上下文（context）。
printed_page 用 page_map.printed()——此处 extracted 必然存在（我们正是从它里面搜的），
page_map 的前置条件天然满足。
只读 EXTRACTED / PAPERS_JSONL / pagemap sidecar，不写任何文件。
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
from textutil import safe_name

# 全角 ASCII 可见区（！～ = U+FF01..U+FF5E）→ 半角，外加全角空格；与 page_map._FW 同思路，
# 这里覆盖整个可见区（page_map 只需要数字）。
_FW = {i + 0xFF01: i + 0x21 for i in range(0x5E)}
_FW[0x3000] = 0x20
# 引号族：中英弯直引号/角引号——引文被抄写时最常被换的就是引号样式，exact 档也剥掉。
# 注意 ＂＇ 是全角、先被 _FW 折到半角，所以这里只需列半角与弯/角引号。
_QUOTES = set(map(ord, "“”‘’「」『』〝〞\"'"))
# fuzzy 档追加剥除的标点（中英对照差异的主要来源）。绝不剥文字/数字。
_PUNCT = set(map(ord, "，。、；：？！（）【】《》〈〉—－…·,.;:?!()[]<>~～/\\|＿_*＊%％#＃&＆+＋=＝^`"))


def _norm_map(text, fuzzy=False):
    """归一化 + 位置映射。返回 (norm_str, pos_list)：norm_str[i] 来自原文 text[pos_list[i]]。
       有了这张映射，归一化串上的命中区间才能换回原文坐标去取 context。"""
    out, pos = [], []
    for i, ch in enumerate(text or ""):
        o = ord(ch)
        o = _FW.get(o, o)
        c = chr(o)
        if c.isspace():
            continue
        if o in _QUOTES:
            continue
        if fuzzy and o in _PUNCT:
            continue
        out.append(c.lower())
        pos.append(i)
    return "".join(out), pos


def norm_text(s, fuzzy=False):
    """只要归一化串（verify_claim 的句子重叠度计算复用它——归一化只此一份，勿另写副本）。"""
    return _norm_map(s, fuzzy)[0]


def _stem_of(key):
    """key → extracted 文件 stem。先试 safe_name(key)（zotero/folder 的常规命名），
       不中再查 papers.jsonl 的 stem 字段（个别篇 stem 与 key 不同，read_source 同款兜底）。"""
    cand = safe_name(key or "")
    if (C.EXTRACTED / f"{cand}.json").exists():
        return cand
    try:
        for line in open(C.PAPERS_JSONL, encoding="utf-8"):
            if not line.strip():
                continue
            p = json.loads(line)
            if p.get("key") == key:
                st = p.get("stem")
                if st and (C.EXTRACTED / f"{st}.json").exists():
                    return st
                break
    except Exception:
        pass
    return None


def _read_doc(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None    # 单篇提取文件损坏不该炸掉整次定位，跳过即可


def _find_in_page(text, nq_exact, nq_fuzzy):
    """在一页文本里找归一化子串。返回 (原文起, 原文止, exact:bool) 或 None。
       先 exact 后 fuzzy——exact 命中说明连标点都对得上，是更硬的核验证据。"""
    ne, pe = _norm_map(text, fuzzy=False)
    j = ne.find(nq_exact)
    if j >= 0:
        return pe[j], pe[j + len(nq_exact) - 1] + 1, True
    if nq_fuzzy:
        nf, pf = _norm_map(text, fuzzy=True)
        j = nf.find(nq_fuzzy)
        if j >= 0:
            return pf[j], pf[j + len(nq_fuzzy) - 1] + 1, False
    return None


CAP_FILES = 500      # 全库扫描上限（契约：cap 500 篇并在结果注明截断）


def locate(quote, key=None, fuzzy=True, max_matches=20, cap_files=CAP_FILES):
    """定位引文。返回 {"matches":[{key,pdf_page,printed_page,exact,context}], "n":int}
       （契约6；截断时附 truncated/note——多出的键只增不改，不破坏契约字段）。
       key 给定只搜单篇；否则全库（按 cap_files 截断，防止超大库把一次调用拖到分钟级）。"""
    q = (quote or "").strip()
    nq_exact = norm_text(q, fuzzy=False)
    if len(nq_exact) < 6:
        # 太短的串（如"第3条"）在全库里到处都是，命中没有核验价值，诚实拒绝
        raise ValueError("引文太短（归一化后不足 6 字），无法可靠定位")
    nq_fuzzy = norm_text(q, fuzzy=True) if fuzzy else ""
    if len(nq_fuzzy) < 4:
        nq_fuzzy = ""            # 引文几乎全是标点：fuzzy 归一化后没剩什么，退纯 exact

    truncated = False
    if key:
        st = _stem_of(key)
        files = [C.EXTRACTED / f"{st}.json"] if st else []
    else:
        files = sorted(C.EXTRACTED.glob("*.json"))
        if len(files) > cap_files:
            files = files[:cap_files]
            truncated = True

    import page_map as PM
    matches = []
    for f in files:
        d = _read_doc(f)
        if not d:
            continue
        k = key or d.get("key") or f.stem
        for pg in (d.get("pages") or []):
            text = pg.get("text") or ""
            if not text:
                continue
            found = _find_in_page(text, nq_exact, nq_fuzzy)
            if not found:
                continue
            i0, i1, exact = found
            ctx = text[max(0, i0 - 60): i1 + 60].replace("\n", " ").strip()
            try:
                pn = int(pg.get("page") or 0)
            except Exception:
                pn = 0
            # 印刷页：pagemap sidecar 按 stem 存，用 f.stem 查最稳（key≠stem 的个别篇也不落空）
            try:
                pr = (PM.printed(f.stem, pn) or {}).get("display") or ""
            except Exception:
                pr = ""
            matches.append({"key": k, "pdf_page": pn, "printed_page": pr,
                            "exact": bool(exact), "context": ctx})
            if len(matches) >= max_matches:
                break
        if len(matches) >= max_matches:
            truncated = truncated or (key is None)   # 命中截断也如实相告
            break

    out = {"matches": matches, "n": len(matches)}
    if truncated:
        out["truncated"] = True
        out["note"] = f"结果已截断（全库扫描上限 {cap_files} 篇 / 命中上限 {max_matches} 条），建议带 key 精确定位"
    return out
