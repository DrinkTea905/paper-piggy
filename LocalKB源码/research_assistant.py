# -*- coding: utf-8 -*-
"""
半自动研究助手编排（Phase B/C/D）：把检索器升格成"按选题梳理带页级引注的资料汇编、诚实标缺口、建议补文献"。
- 能力二 digest：召回 → page_map 印刷页 → RCS 综述(带印刷页引注) → 覆盖评级 → 缺口标注 → 存 kind=digest 页。
- 能力一 scope：主题 → 范围映射 + 选题拆解 + 标题候选 + 三级大纲(★/☆) → 存 kind=outline 页。
- 能力三 suggest_sources：覆盖评估 + 脚注引文挖掘缺失文献 + 补检索关键词 + 库内错配（按期刊层级排）。
诚信红线（§7）作为系统提示内建进每次合成：观点归属/争议双呈/缺口诚实(库内没有≠学界空白)/AI披露/判例不生成。
规则做格式(cite_format/page_map/覆盖统计)、模型做实质(RCS 综述)。无 LLM key 时退化为"带源证据清单"（诚实标注）。
"""
import sys, re, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import llm as L
import cite_format as CF
import wiki_store as W

# 覆盖度图例（对标 docx）
COV = {"◎": "充分", "○": "部分", "△": "薄弱", "▲": "基本缺失", "▽": "零星"}

INTEGRITY = (
    "学术诚信红线（务必遵守）：\n"
    "1) 观点归属：谁主张什么要清楚，绝不把转引者的发挥归于原作者；库内只有转引命中时，提示『疑似转引，请核原著』，不直接归给原作者。\n"
    "2) 立场不可混为通说：并存的不同立场要分别标注归属，不写成『通说认为』。\n"
    "3) 争议双呈：真实分歧要先陈两方、各附出处，再（如需）表本文倾向；绝不落成『A错B对』断言。\n"
    "4) 缺口诚实：库内没有≠学界空白；不足处写『在本库覆盖范围内、截至目前未见充分讨论』，禁用『首次/空白/无人研究』。\n"
    "5) 比较法降调：域外材料若仅中文转引，用『据×××介绍』，不以『德国法规定』口吻断言。\n"
    "6) 判例/法条不生成：只综述库内期刊论文，任何案号/司法解释一律不凭生成产出。\n"
)

DIGEST_SYS = (
    "你是严谨的中文法学文献研究助手。请**只依据下面提供的、带引注的文献片段**，就「{subject}」写一节综述性 prose。\n"
    "严格要求：\n"
    "1) 只允许使用给定片段里的信息，禁止无出处生成句；每个论点后**紧跟原样保留片段给出的引注**（形如 [作者《题名》，刊名，第X页]），同一论点多处出处连续排列。\n"
    "2) 综述要成段落 prose（非罗列），围绕子题组织论点、立场谱系与主要争点。\n"
    "3) 信息不足处如实说明，不臆造。\n"
    + INTEGRITY +
    "\n=== 带引注的文献片段 ===\n{ctx}"
)


def _digest_id(query):
    h = hashlib.sha1(re.sub(r"\s+", " ", (query or "").strip()).lower().encode("utf-8")).hexdigest()[:8]
    return f"digest-{h}"


def _recall(query, topk):
    """召回命中 chunk（剔除 wiki 行防自证）。返回 hits。"""
    try:
        import retriever as R
        hits = R.search(query, topk, "blend") if R.STATE.get("ready") else []
    except Exception:
        hits = []
    return [h for h in hits if not h.get("is_wiki")]


def _coverage(hits):
    """按命中密度 + 高层级命中数给覆盖评级。返回 (symbol, label, n, n_high)。"""
    n = len(hits)
    # 高层级：weight_tier ∈ {权威,准权威,核心} 或 journal_weight≥0.85
    n_high = sum(1 for h in hits if (h.get("weight_tier") in ("权威", "准权威", "核心"))
                 or (isinstance(h.get("journal_weight"), (int, float)) and h.get("journal_weight", 0) >= 0.85))
    if n >= 8 and n_high >= 2:
        sym = "◎"
    elif n >= 4:
        sym = "○"
    elif n >= 2:
        sym = "△"
    elif n == 1:
        sym = "▲"
    else:
        sym = "▽"
    return sym, COV[sym], n, n_high


def _gap_note(subject, hits, sym):
    """诚实的资料缺口提示（不夸大、不把库内没有说成学界空白）。"""
    n = len(hits)
    if n == 0:
        return f"（资料缺口：在本库覆盖范围内，截至目前未检索到直接讨论「{subject}」的期刊论文，建议就此子题补检索或补入文献。）"
    if sym in ("△", "▲", "▽"):
        top = "、".join(f"《{(h.get('title') or '')[:16]}》" for h in hits[:3] if h.get("title"))
        return f"（资料缺口：本节在本库内仅 {n} 篇较相关（如 {top}），直接论述较薄弱，建议就此子题补检索。）"
    return f"（本节在本库内命中 {n} 篇，覆盖{COV[sym]}；仍建议核对观点归属与是否存在转引。）"


def _build_ctx(hits, max_chars=1100):
    """把命中证据组装成"带引注的片段"喂给 LLM：每段 = 印刷页引注 + 正文片段。"""
    lines = []
    for h in hits:
        cite = CF.compact(h)
        text = (h.get("context") or h.get("text") or "")[:max_chars].strip()
        lines.append(f"{cite}\n{text}")
    return "\n\n".join(lines) or "（暂无检索结果）"


def _resolve_llm(llm):
    base, model = L.resolve(llm.get("provider", ""), llm.get("base_url", ""), llm.get("model", ""))
    key = llm.get("api_key", "")
    if not key:
        try:
            import settings as S
            key = (S.api_conf() or {}).get("key", "")
        except Exception:
            pass
    return base, model, key


def _fallback_body(subject, hits):
    """无 LLM key 时的退化产物：带源证据清单（诚实标注非合成综述）。"""
    out = [f"> ⚠ 未配置 LLM，本节为**带源证据清单**（非合成综述）。每条为库内命中片段 + 印刷页引注，供人工整理。\n"]
    for h in hits:
        cite = CF.compact(h)
        snip = (h.get("text") or "")[:180].strip().replace("\n", " ")
        out.append(f"- {snip} {cite}")
    return "\n".join(out)


def _empty_body(subject):
    """R5：库内无命中——与「未配 key」分开：这是「本库暂无相关文献」，不是缺 LLM。"""
    return (f"> ℹ 本库暂无与「{subject}」直接相关的文献命中，未能生成资料汇编。\n\n"
            "建议：换用更贴近文献表述的关键词重试；或先补检索/补入相关文献后再试。")


def _degraded_gb(gb):
    """R6：判断一页是否为「降级产物」（未配 key / LLM 失败 / 无命中）。
       这类页不应被当权威缓存长期复用——命中缓存时自动重生（修好 key/补文献后即刷新）。
       成功的 LLM 综述 generated_by=模型名，不以 fallback( 开头、也不等于 no-hits/no-key。"""
    gb = gb or ""
    return gb.startswith("fallback(") or gb in ("no-hits", "no-key")


def _indexed_of(page_id):
    """wiki-cached-indexed-true：命中缓存时按实回填 indexed（该页是否真在检索内存表里）。"""
    try:
        return W.is_indexed(page_id)
    except Exception:
        return False


def digest(query, topk=14, llm=None, force=False, by_agent=False):
    """能力二：给一个子题 query → 一节带印刷页引注的综述 + 覆盖评级 + 缺口。存 kind=digest 页。"""
    llm = llm or {}
    query = (query or "").strip()
    if not query:
        raise ValueError("空 query")
    page_id = _digest_id(query)
    if not force:
        cached = W.index_map().get(page_id)
        # R6：只有非降级页才复用缓存；降级页（缺 key/LLM 失败/无命中）落到下方重生。
        if cached and not _degraded_gb(cached.get("generated_by", "")):
            m = dict(cached); m["cached"] = True; m["indexed"] = _indexed_of(page_id)
            return m
    hits = _recall(query, topk)
    sym, label, n, n_high = _coverage(hits)
    ctx = _build_ctx(hits)
    base, model, key = _resolve_llm(llm)
    if not hits:                       # R5：库内无命中 ≠ 未配 LLM，分开提示
        body = _empty_body(query); gen_by = "no-hits"
    elif key:
        messages = [{"role": "system", "content": DIGEST_SYS.format(subject=query, ctx=ctx)},
                    {"role": "user", "content": f"请就「{query}」写这一节综述，每个论点后保留原引注。"}]
        try:
            body = L.chat_once(messages, base, key, model, temperature=0.2, timeout=180)
            gen_by = model
        except Exception as e:
            body = _fallback_body(query, hits); gen_by = f"fallback({e.__class__.__name__})"
    else:
        body = _fallback_body(query, hits); gen_by = "fallback(no-key)"
    # 覆盖评级 + 缺口 + AI 披露，拼进正文尾
    body = (body.strip()
            + f"\n\n**知识库覆盖**：{sym} {label}（本库命中 {n} 篇，其中高层级 {n_high} 篇）。"
            + f"\n{_gap_note(query, hits, sym)}"
            + "\n\n> *生成式 AI 使用声明（草稿）：本节由本地检索库召回、AI 辅助梳理成带源初稿，"
              "参与阶段=检索/材料梳理；引注页码为期刊印刷页码（标『页码推算』者为连续性推算，请核对）；"
              "内容与观点归属由作者核校负责，判例/法条未由 AI 生成。*")
    sources = [{"key": h.get("key", ""), "citation": h.get("citation", "")} for h in hits if h.get("key")]
    title = f"资料汇编·{query[:30]}"
    meta = W.save_research_page(page_id, "digest", title, query, body, sources,
                               generated_by=gen_by, by_agent=by_agent)
    meta["cached"] = False
    meta["degraded"] = _degraded_gb(gen_by)      # R6：降级页标记，前端可提示、下次命中自动重试
    meta["coverage"] = {"symbol": sym, "label": label, "n": n, "n_high": n_high}
    return meta


# ═══ 能力一：选题/框架/大纲（Phase C）═══════════════════════════════
SCOPE_SYS = (
    "你是资深中文法学研究助手。基于下面给出的、库内真实召回的文献线索，为研究主题「{topic}」产出：\n"
    "1) 选题拆解：按『方法论视角 / 研究对象 / 制度落点』三层给关键词结构；问题意识；核心命题（可支撑的方向）；可能的创新点（每条尽量挂一个召回到的文献题名）。\n"
    "2) 标题参考：3-5 个主/副标题候选。\n"
    "3) 三级大纲：引言/各章/结语，每个大纲节点后用（★核心/☆辅助）标注它可依托的召回文献题名（只用给定线索里的真实篇，不臆造）。\n"
    + INTEGRITY +
    "\n=== 库内召回线索（题名·期刊·印刷页引注） ===\n{ctx}"
)


def scope(topic, topk=20, llm=None, force=False, by_agent=False):
    """能力一：主题 → 范围映射 + 选题拆解 + 标题 + 三级大纲。存 kind=outline 页。"""
    llm = llm or {}
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("空主题")
    page_id = "outline-" + hashlib.sha1(re.sub(r"\s+", " ", topic.lower()).encode("utf-8")).hexdigest()[:8]
    if not force:
        cached = W.index_map().get(page_id)
        # R6：降级页不复用，落到下方重生（修好 key/补文献后自动刷新）。
        if cached and not _degraded_gb(cached.get("generated_by", "")):
            m = dict(cached); m["cached"] = True; m["indexed"] = _indexed_of(page_id)
            return m
    hits = _recall(topic, topk)
    # 范围映射：命中的 tier 分布
    tiers = {}
    for h in hits:
        t = h.get("weight_tier") or h.get("journal_tier") or "未知"
        tiers[t] = tiers.get(t, 0) + 1
    ctx = "\n".join(f"{i+1}. {CF.compact(h)}" for i, h in enumerate(hits))
    base, model, key = _resolve_llm(llm)
    if not hits:                       # R5：库内无命中 ≠ 未配 LLM
        body = _scope_empty(topic); gen_by = "no-hits"
    elif key:
        messages = [{"role": "system", "content": SCOPE_SYS.format(topic=topic, ctx=ctx)},
                    {"role": "user", "content": f"请就「{topic}」产出选题拆解、标题参考与三级大纲。"}]
        try:
            body = L.chat_once(messages, base, key, model, temperature=0.3, timeout=180)
            gen_by = model
        except Exception as e:
            body = _scope_fallback(topic, hits, tiers); gen_by = f"fallback({e.__class__.__name__})"
    else:
        body = _scope_fallback(topic, hits, tiers); gen_by = "fallback(no-key)"
    body = (f"> **研究主题**：{topic}　本库相关命中 {len(hits)} 篇，层级分布："
            + "、".join(f"{k} {v}" for k, v in sorted(tiers.items())) + "\n\n"
            + body.strip()
            + "\n\n> *AI 使用声明：本大纲为 AI 辅助的选题启发初稿，论证主线须由作者自定；★/☆ 标注仅供参考。*")
    sources = [{"key": h.get("key", ""), "citation": h.get("citation", "")} for h in hits if h.get("key")]
    meta = W.save_research_page(page_id, "outline", f"选题框架·{topic[:28]}", topic, body, sources,
                               generated_by=gen_by, by_agent=by_agent)
    meta["cached"] = False
    meta["degraded"] = _degraded_gb(gen_by)      # R6：降级页标记
    return meta


def _scope_fallback(topic, hits, tiers):
    out = [f"> ⚠ 未配置 LLM，以下为**库内召回线索清单**（非合成大纲），供人工拟题/搭框架。\n",
           f"### 本库与「{topic}」相关的命中（按检索相关性）"]
    for i, h in enumerate(hits, 1):
        out.append(f"{i}. {CF.compact(h)}")
    return "\n".join(out)


def _scope_empty(topic):
    """R5：库内无命中——与「未配 LLM」分开的诚实提示。"""
    return (f"> ℹ 本库暂无与「{topic}」直接相关的文献命中，未能生成选题框架。\n\n"
            "建议：换用更贴近文献表述的关键词重试；或先补检索/补入相关文献后再试。")


# ═══ 能力三：建议新增文献（Phase D）═══════════════════════════════
# 脚注引文粗抽：'参见/见/转引自 作者：《题名》，载《刊名》…' 之类
_RE_CITE = re.compile(r"(?:参见|见|另见|又见|转引自)\s*([^，：:《]{1,20})[：:]?\s*《([^》]{2,60})》")


def _mine_citations(hits, limit_pdfs=20):
    """从命中文献的 extracted 全文里粗抽被引文献（脚注模式）。返回 [{author,title,freq}]。"""
    import json
    from textutil import safe_name
    found = {}
    keys = []
    for h in hits:
        k = h.get("key")
        if k and k not in keys:
            keys.append(k)
    for k in keys[:limit_pdfs]:
        ex = C.EXTRACTED / f"{safe_name(k)}.json"
        if not ex.exists():
            continue
        try:
            d = json.loads(ex.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = "\n".join(p.get("text", "") for p in (d.get("pages") or []))
        for m in _RE_CITE.finditer(text):
            author = m.group(1).strip().strip("，,。")
            title = m.group(2).strip()
            if len(title) < 3:
                continue
            key2 = (author, title)
            found[key2] = found.get(key2, 0) + 1
    items = [{"author": a, "title": t, "freq": n} for (a, t), n in found.items()]
    items.sort(key=lambda x: -x["freq"])
    return items


def suggest_sources(topic, topk=20, llm=None):
    """能力三：覆盖评估 + 脚注引文挖掘缺失文献 + 库内错配。按被引频次排。不写库（读）。"""
    llm = llm or {}
    hits = _recall(topic, topk)
    sym, label, n, n_high = _coverage(hits)
    mined = _mine_citations(hits)
    # 与库内 papers.jsonl 比对：题名/作者已有的剔除（粗匹配）
    have_titles = set()
    try:
        import retriever as R
        for p in (R.M.get("papers") or {}).values():
            t = (p.get("title") or "").strip()
            if t:
                have_titles.add(re.sub(r"\s+", "", t))
    except Exception:
        pass
    missing = []
    for it in mined:
        norm_t = re.sub(r"\s+", "", it["title"])
        if norm_t and not any(norm_t in ht or ht in norm_t for ht in have_titles):
            # 估算期刊层级（若脚注给了刊名——此处粗抽未含刊名，留待增强）
            missing.append(it)
    # 库内错配：有 PDF 但未深索的相关篇
    mismatch = []
    try:
        import retriever as R, textutil as T
        deepk = set()
        ek = C.STATE / "embedded_keys.txt"
        if ek.exists():
            deepk = set(ek.read_text(encoding="utf-8").split())
        for h in hits:
            k = h.get("key", "")
            if k and h.get("has_pdf") and T.safe_name(k) not in deepk:
                mismatch.append({"key": k, "title": h.get("title", ""), "citation": h.get("citation", "")})
    except Exception:
        pass
    return {
        "topic": topic,
        "coverage": {"symbol": sym, "label": label, "n": n, "n_high": n_high},
        "missing_cited": missing[:20],       # 被库内引用但库中缺失的文献（按被引频次）
        "mismatch_undeep": mismatch[:20],    # 已有 PDF 但未深索的相关篇
        "gap_note": _gap_note(topic, hits, sym),
    }
