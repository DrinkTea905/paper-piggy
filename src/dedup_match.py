"""外部题录 × 库内题录的查重匹配（纯函数，无 IO、无网络、无 LLM）。

**为什么需要它**：Agent 从库外（知网、万方、Google Scholar…）一次检索回三十条题录，
必须先知道「哪些库里已经有了」才能决定下载哪些——否则要么重复下载脏库，要么漏掉。

**为什么不能用 search_localkb 干这事**：那是语义检索，同题不同篇会拿高分，
拿它查重必然误判。查重要的是**题名同一性**，不是语义相近。

**为什么不直接用 /papers?query=**：那个匹配是「查询串整体子串命中」，一次只查一条。
外部题录与库内题录在标点、全半角、副标题、书名号上几乎必然有差异，精确子串会大面积漏判。

判定分三档，规则全部可解释（见 match_one 的 reason 字段）：
  exists   —— 库里已有，别再下
  probable —— 疑似同一篇，交给人眼确认
  missing  —— 库里没有，可以下
"""

import re
import unicodedata

# 归一化时整体丢弃的字符类：标点、符号、空白。
# 中文标题里的《》「」——：，全角/半角引号，都在这一网里。
_DROP = re.compile(r"[\s\u3000-\u303f\uff00-\uffef!-/:-@\[-`{-~·—–－「」『』《》〈〉【】]+")

# 作者串的分隔符：Zotero 用 "; "，知网用 "；" 或 "," 或 "、"，还有 " and "。
_AUTHOR_SPLIT = re.compile(r"[;；,，、/|]+|\sand\s|\s&\s")

# 作者名里的角色后缀，比对前去掉，否则「张三 著」与「张三」判不等。
_AUTHOR_NOISE = re.compile(r"(等|著|编|主编|编著|译|校|撰|着)+$")

# 判定阈值。都是字符 bigram 的 Dice 系数（0~1），不是编辑距离——
# 中文标题换一两个虚词、加个副标题，Dice 掉得比编辑距离温和，更贴合"同一篇"的直觉。
STRONG_DICE = 0.85   # 加一条旁证（作者/年份/前缀包含）即判 exists
WEAK_DICE = 0.70     # 单凭标题相似度，只敢报 probable

# 候选粗筛：先按共享 bigram 数取前 N 篇再精算，避免全库两两比对。
_CANDIDATE_POOL = 40


def normalize_title(s):
    """标题归一化：全角折半角、大小写折叠、去掉全部标点与空白。

    只留下「实词字符序列」，因为外部库与 Zotero 在标点上的差异是系统性的
    （知网爱用「——」接副标题，Zotero 里常被存成「：」或直接丢掉）。
    """
    s = unicodedata.normalize("NFKC", str(s or ""))
    return _DROP.sub("", s).casefold()


def _bigrams(norm):
    """归一化标题的字符 bigram 集合。单字标题退化为它自己，避免空集。"""
    if len(norm) < 2:
        return {norm} if norm else set()
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


def dice(a, b):
    """Dice 系数：2|A∩B| / (|A|+|B|)。两个空集算 0，不算 1。"""
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def normalize_authors(s):
    """作者串 → 归一化人名集合。

    中文名整名比对（「张三」）；西文名只留姓（"Smith, John" / "John Smith" → "smith"），
    因为外部库与 Zotero 对名字缩写（John / J.）的处理不一致，姓才是稳定的那部分。
    """
    out = set()
    for raw in _AUTHOR_SPLIT.split(unicodedata.normalize("NFKC", str(s or ""))):
        name = _AUTHOR_NOISE.sub("", _DROP.sub("", raw).casefold())
        if not name:
            continue
        if re.search(r"[a-z]", name):
            # 西文：_DROP 已经把逗号和空格吃掉了，无法再分姓名，
            # 所以这里退回原串按空白/逗号切一次，取最长的一段当姓。
            parts = [p for p in re.split(r"[\s,]+", raw.casefold().strip()) if p]
            if parts:
                out.add(max(parts, key=len).strip(".").casefold())
        else:
            out.add(name)
    return out


def _year(v):
    """从任意形态的年份字段里抠出四位年；抠不出返回空串。"""
    m = re.search(r"(1[5-9]\d{2}|20\d{2})", str(v or ""))
    return m.group(1) if m else ""


def build_index(papers):
    """把库内题录建成查重索引。

    papers：可迭代的题录 dict，需要 key / title；author(s)、year 有则用。
    随 papers.jsonl 变化重建即可（调用方负责缓存，本模块不碰文件）。
    """
    entries = []
    postings = {}
    exact = {}
    for p in papers:
        title = str(p.get("title") or "")
        norm = normalize_title(title)
        if not norm:
            continue          # 无题名的条目无法参与题名查重，直接跳过
        idx = len(entries)
        grams = _bigrams(norm)
        entries.append({
            "key": p.get("key", ""),
            "title": title,
            "norm": norm,
            "grams": grams,
            "authors": normalize_authors(p.get("authors") or p.get("author") or ""),
            "year": _year(p.get("year")),
            "journal": str(p.get("journal") or ""),
        })
        exact.setdefault(norm, []).append(idx)
        for g in grams:
            postings.setdefault(g, []).append(idx)
    return {"entries": entries, "postings": postings, "exact": exact}


def _candidates(index, grams):
    """bigram 倒排粗筛：返回共享 bigram 最多的若干条下标。"""
    hits = {}
    postings = index["postings"]
    for g in grams:
        for idx in postings.get(g, ()):
            hits[idx] = hits.get(idx, 0) + 1
    if not hits:
        return []
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    return [idx for idx, _n in ranked[:_CANDIDATE_POOL]]


def match_one(index, title, authors="", year=""):
    """查一条外部题录。返回 {verdict, reason, matches:[…]}。

    matches 按可信度降序，每条带 key/title/score，方便 Agent 直接引用或让用户核对。
    """
    norm = normalize_title(title)
    if not norm:
        return {"verdict": "missing", "reason": "标题为空，无法查重", "matches": []}

    q_grams = _bigrams(norm)
    q_authors = normalize_authors(authors)
    q_year = _year(year)

    scored = []
    for idx in _candidates(index, q_grams):
        e = index["entries"][idx]
        d = dice(q_grams, e["grams"])
        if d < WEAK_DICE and not (norm in e["norm"] or e["norm"] in norm):
            continue
        author_hit = bool(q_authors & e["authors"])
        year_hit = bool(q_year and q_year == e["year"])
        # 副标题被一方截断是最常见的差异形态（知网带「——副标题」，Zotero 里常没有）。
        # 只有较短的那个够长时才认，否则「论」「研究」这种碎片会到处包含。
        contains = (norm in e["norm"] or e["norm"] in norm) and min(len(norm), len(e["norm"])) >= 6
        scored.append((d, author_hit, year_hit, contains, e))

    if not scored:
        return {"verdict": "missing", "reason": "库内无题名相近的条目", "matches": []}

    scored.sort(key=lambda t: (-t[0], not t[1], not t[2]))
    matches = [{"key": e["key"], "title": e["title"], "year": e["year"],
                "journal": e["journal"], "score": round(d, 3),
                "author_hit": ah, "year_hit": yh}
               for d, ah, yh, _c, e in scored[:5]]

    d, author_hit, year_hit, contains, top = scored[0]
    if top["norm"] == norm:
        return {"verdict": "exists", "reason": "题名归一化后完全一致", "matches": matches}
    if d >= STRONG_DICE and (author_hit or year_hit or contains):
        why = "作者一致" if author_hit else ("年份一致" if year_hit else "一方为另一方的正/副标题")
        return {"verdict": "exists", "reason": f"题名高度相似（{d:.2f}）且{why}", "matches": matches}
    if contains and (author_hit or year_hit):
        why = "作者一致" if author_hit else "年份一致"
        return {"verdict": "exists", "reason": f"一方为另一方的正/副标题且{why}", "matches": matches}
    return {"verdict": "probable",
            "reason": f"题名相似（{d:.2f}）但缺少作者或年份旁证，需人工确认",
            "matches": matches}


def check_batch(papers, items):
    """批量查重。papers=库内题录可迭代对象；items=[{title,authors,year}…]。

    返回与 items 等长的结果列表（顺序一一对应），外加一份计数汇总。
    """
    index = build_index(papers)
    results = []
    counts = {"exists": 0, "probable": 0, "missing": 0}
    for it in items:
        r = match_one(index,
                      it.get("title", ""),
                      it.get("authors") or it.get("author") or "",
                      it.get("year", ""))
        r["title"] = str(it.get("title") or "")
        results.append(r)
        counts[r["verdict"]] += 1
    return {"results": results, "counts": counts}
