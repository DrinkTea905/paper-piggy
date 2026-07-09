# -*- coding: utf-8 -*-
"""
综合层（wiki）存储 —— 把 LLM 的综合**持久化**成带引用、可累积、互链的页面。

- Phase 0：把一次 /chat 问答存成 answer 页（markdown + YAML frontmatter）→ 更新 index.json
  → 嵌入进 LanceDB（chunk_id 以 "::wiki" 结尾，row_type="wiki"）从此可被检索。
- 复用 sac.py 的"LLM→幂等 JSON→原子写"写回模式；嵌入/入表委托 retriever（它持有
  M["tbl"]/M["embed"]，保证**进程内即时可搜**，无需重启/重建索引）。
- 只写 DATA/wiki/，随便删不影响文献库/Zotero（config §0 独立性保证）。

provenance 命脉：每页带 sources(论文 key + 页级引用) + generated_by(模型) + generated_at(时间戳)
+ stale(是否过时)。index.json 是元数据的权威事实来源；.md 是给人/Obsidian 读的渲染件。
"""
import sys, os, json, time, hashlib, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import llm as L

WIKI_MD_SEED = """# 本地知识库 · 综合层（Wiki）约定 — schema v0

> 本目录（data/wiki/）是"综合层"：把 LLM 对文献的理解**持久化**成可累积、带引用、互链的页面。
> 它是文献库之上的**附加缓存**，不是替代——删除本目录不影响文献库/Zotero/索引。

## 页面种类（kind）
- answer   一次 /chat 问答沉淀下来的综述（Phase 0）
- concept  概念页（按需生成 + 缓存，Phase 1）
- topic    主题页（对应 AI 主题聚类的一簇，Phase 1）

## 每页结构
YAML frontmatter（id/kind/title/sources/generated_at/generated_by/stale/links）+ markdown 正文。
- sources：本页综合所依据的论文 key（provenance 命脉；每个 key 可经 _page_cite 回溯到页码）。
- generated_at / generated_by：生成时间与模型（可信度审计；综合质量随模型档位变化）。
- stale：被新增/更新论文影响、待重生时置 true（Phase 3 lint；Phase 0/1 暂不自动置位）。
- links：交叉链接到其它 wiki 页 id（把主题"集合"补成"图"）。

## 写回纪律（给人与 agent）
1. 每个论断后带 [n] 引用，n 对应"参考来源"里的论文；不臆造、不给无出处的断言。
2. 综合是快照不是定论：读者以原文页码为准；旧综合可能已过时（检索时会标注并降权）。
3. 只写 data/wiki/；绝不改动文献库、索引、Zotero。
4. 矛盾/争议只作"未核实"的只读提示，绝不落成 wiki 断言（见设计文档 §8）。

## 检索中的表现
每页作为一行进同一张 LanceDB 表（chunk_id 以 "::wiki" 结尾）参与召回；命中时标注
"（来自既有综合，可能已过时）"，stale 页在排序中降权。
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def ensure_scaffold():
    for d in (C.WIKI_DIR, C.WIKI_ANSWERS_DIR, C.WIKI_CONCEPTS_DIR, C.WIKI_TOPICS_DIR,
              C.WIKI_DIGEST_DIR, C.WIKI_OUTLINE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if not C.WIKI_SCHEMA_MD.exists():
        _atomic_write(C.WIKI_SCHEMA_MD, WIKI_MD_SEED)


# ═══ index.json（元数据权威事实来源）════════════════════════════════
def load_index():
    if C.WIKI_INDEX.exists():
        try:
            return json.loads(C.WIKI_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"pages": [], "by_source": {}, "updated_at": ""}


def _save_index(idx):
    idx["updated_at"] = _now()
    _atomic_write(C.WIKI_INDEX, json.dumps(idx, ensure_ascii=False, indent=1))


def index_map():
    """id -> 页元数据 dict（供 retriever 载入 M["wiki"]，做标注/降权/展示）。"""
    return {p["id"]: p for p in load_index().get("pages", [])}


def is_indexed(page_id):
    """该 wiki 页是否**真的**在检索内存表里（full 模式入表成功才算）。
       wiki-cached-indexed-true：命中缓存时据此按实回填 indexed，
       避免 light 模式/入表失败时仍谎报 indexed=True。"""
    try:
        import retriever as R
        return f"{page_id}::wiki" in (R.M.get("records") or {})
    except Exception:
        return False


def _upsert_index(meta):
    idx = load_index()
    pages = [p for p in idx.get("pages", []) if p.get("id") != meta["id"]]
    entry = {k: meta.get(k) for k in
             ("id", "kind", "title", "subject", "sources", "generated_at", "generated_by", "stale", "by_agent", "links")}
    pages.append(entry)
    idx["pages"] = pages
    bs = {}   # by_source 反查：某论文被哪些 wiki 页引用（供 stale 标记）
    for p in pages:
        for s in p.get("sources", []):
            key = s.get("key") if isinstance(s, dict) else s
            if key:
                bs.setdefault(key, [])
                if p["id"] not in bs[key]:
                    bs[key].append(p["id"])
    idx["by_source"] = bs
    _save_index(idx)


# ═══ id / 标题 / 路径 ════════════════════════════════════════════
def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _answer_id(query):
    h = hashlib.sha1(_norm(query).encode("utf-8")).hexdigest()[:8]
    return f"answer-{h}"


def _title_from(query, answer):
    """Phase 0 标题：用问题首句（Phase 1 概念/主题页由 LLM 命名）。"""
    base = (query or answer or "").strip().replace("\n", " ")
    base = re.sub(r"[?？。.!！]+$", "", base)
    return base[:40] or "本地综合"


def kind_dir(kind):
    return {"answer": C.WIKI_ANSWERS_DIR, "concept": C.WIKI_CONCEPTS_DIR,
            "topic": C.WIKI_TOPICS_DIR, "digest": C.WIKI_DIGEST_DIR,
            "outline": C.WIKI_OUTLINE_DIR}.get(kind, C.WIKI_ANSWERS_DIR)


def page_path(page_id, kind):
    return kind_dir(kind) / f"{page_id}.md"


# ═══ 页面渲染（frontmatter + markdown；不依赖 pyyaml）═══════════════
def _frontmatter(meta):
    def jl(items):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in items) + "]"
    src_keys = [(s.get("key") if isinstance(s, dict) else s) for s in meta.get("sources", [])]
    return "\n".join([
        "---",
        f"id: {meta['id']}",
        f"kind: {meta['kind']}",
        f"title: {json.dumps(meta.get('title', ''), ensure_ascii=False)}",
        f"sources: {jl([k for k in src_keys if k])}",
        f"generated_at: {meta.get('generated_at', '')}",
        f"generated_by: {meta.get('generated_by', '')}",
        f"stale: {'true' if meta.get('stale') else 'false'}",
        f"links: {jl(meta.get('links', []))}",
        "---",
    ])


def _render_md(meta, question, answer, sources):
    body = [_frontmatter(meta), "", f"# {meta.get('title', '')}", ""]
    if question:
        body += [f"> **研究问题**：{question}", ""]
    body.append(answer.strip())
    if sources:
        body += ["", "---", "", "**参考来源**（可回溯到论文页码）：", ""]
        for i, s in enumerate(sources, 1):
            body.append(f"{i}. {s.get('citation', '') or s.get('key', '')}")
    body += ["", f"*（本页为本地综合，生成于 {meta.get('generated_at', '')} · 基于 "
             f"{len(sources)} 篇 · 模型 {meta.get('generated_by', '') or '未知'}；"
             f"可能已过时，请以原文为准。）*"]
    return "\n".join(body)


def _plain_body(answer, sources):
    """给 reranker/嵌入用的纯文本（答案 + 来源引用），比 markdown 更利于语义匹配。"""
    src = " ".join(s.get("citation", "") for s in sources)
    return (answer + "\n" + src).strip()


def _resolve_citation(key, fallback=""):
    """服务端权威解析论文 key -> 页级引用（委托 retriever 的 papers.jsonl / _page_cite）。"""
    try:
        import retriever as R
        p = (R.M.get("papers") or {}).get(key)
        if p:
            return R._page_cite(p)
    except Exception:
        pass
    return fallback or key


def _norm_sources(sources):
    """规整 sources：去重 key，服务端权威解析页级引用（客户端 citation 作兜底）。"""
    seen, out = set(), []
    for s in (sources or []):
        if isinstance(s, str):
            s = {"key": s, "citation": ""}
        k = (s.get("key") or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append({"key": k, "citation": _resolve_citation(k, s.get("citation", ""))})
    return out


def _persist_page(page_id, kind, title, subject, body, norm_sources, generated_by="", by_agent=False):
    """写盘 + 更新 index.json + 嵌入入表。answer/concept/topic 三种页共用。返回 page-meta。
    by_agent=True 标记"agent 经 MCP 写回、未经人工核验"（前端标 🤖 徽章，供事后一键剔除）。"""
    meta = {
        "id": page_id, "kind": kind,
        "title": (title or _title_from(subject, body)).strip(),
        "subject": subject, "sources": norm_sources,
        "generated_at": _now(), "generated_by": generated_by or "",
        "stale": False, "by_agent": bool(by_agent), "links": [], "query": subject,
    }
    _atomic_write(page_path(page_id, kind), _render_md(meta, subject, body, norm_sources))
    _upsert_index(meta)
    indexed = False
    try:
        import retriever as R
        indexed = R.index_wiki_page(page_id, meta["title"], _plain_body(body, norm_sources), meta)
    except Exception as e:
        print(f"[wiki] 入表失败（仅存盘，重建索引后可检索）：{e}", file=sys.stderr, flush=True)
    meta["indexed"] = bool(indexed)
    return meta


# ═══ 对外：保存一次问答为 answer 页（Phase 0 核心）═══════════════════
def save_answer(query, answer, sources=None, generated_by="", title="", by_agent=False):
    """把一次问答存成 answer 页 + 入表。sources: list of {key, citation}（或纯 key 字符串）。
    幂等：同一问题 → 同一 id → 覆盖旧页（含重生更新 generated_at）。
    by_agent=True：agent 经 MCP save_synthesis 写回（默认采纳、立即可检索；标 🤖 待人复看/剔除）。"""
    ensure_scaffold()
    query = (query or "").strip()
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("空答案，拒绝沉淀")
    page_id = _answer_id(query or answer[:64])
    return _persist_page(page_id, "answer", title, query, answer,
                         _norm_sources(sources), generated_by, by_agent)


# ═══ 对外：研究助手写回 digest / outline 页（Phase B/C）═══════════════
def save_research_page(page_id, kind, title, subject, body, sources=None, generated_by="", by_agent=False):
    """把研究助手产出的资料汇编(digest)/大纲(outline)持久化成 wiki 页 + 入表可检索。
       sources: list of {key, citation} 或纯 key。复用 _persist_page（answer/concept 同一套）。"""
    ensure_scaffold()
    if not (body or "").strip():
        raise ValueError("空正文，拒绝写回")
    return _persist_page(page_id, kind, title, subject, body, _norm_sources(sources), generated_by, by_agent)


# ═══ 对外：按需生成概念页 / 主题页（Phase 1）——检索证据 → LLM 综合 + LLM 命名 → 缓存 ═══
SYN_SYS = (
    "你是严谨的法学文献研究助手。请**只依据下面提供的文献片段**，就「{subject}」写一篇结构化中文综述。\n"
    "严格要求：\n"
    "1) 第一行只输出标题，格式必须为「标题：<一个准确、精炼的中文标题>」；\n"
    "2) 随后正文分点综述：核心问题、主要立场 / 学理谱系、主要争点；每个论断后用 [编号] 标注来源；\n"
    "3) 只依据给定片段，信息不足处如实说明，不臆造、不给无出处的论断。\n\n"
    "=== 文献片段 ===\n{ctx}"
)


def _slug(s):
    s = re.sub(r'[\\/:*?"<>|\s]+', "", (s or "").strip())
    return s[:30] or ("h" + hashlib.sha1((s or "x").encode("utf-8")).hexdigest()[:8])


def _load_ai_topics():
    f = C.CATEGORIES_DIR / "ai_topics.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"topics": []}


def _gather_evidence(query, topk):
    """用现有检索（零改动）召回论据；剔除既有综合页，避免拿旧综合当证据造成漂移/复利。"""
    try:
        import retriever as R
        hits = R.search(query, topk, "blend") if R.STATE.get("ready") else []
    except Exception:
        hits = []
    hits = [h for h in hits if not h.get("is_wiki")]
    ctx = "\n\n".join(
        f"[{i+1}] {h.get('citation','')}\n{(h.get('context') or h.get('text') or '')[:1000]}"
        for i, h in enumerate(hits)) or "（暂无检索结果）"
    seen, srcs = set(), []
    for h in hits:
        k = h.get("key", "")
        if k and k not in seen:
            seen.add(k); srcs.append({"key": k, "citation": h.get("citation", "")})
    return ctx, srcs


def _parse_title(text, fallback):
    """从 LLM 输出解析「标题：xxx」首行 + 正文（用户拍板：概念/主题页标题由 LLM 命名）。"""
    lines = (text or "").strip().splitlines()
    title, start = fallback, 0
    for i, ln in enumerate(lines[:3]):
        m = re.match(r'^\s*(?:#+\s*)?\**\s*标题[:：]\s*(.+?)\**\s*$', ln)
        if m:
            title = m.group(1).strip().strip('《》""\' '); start = i + 1; break
    body = "\n".join(lines[start:]).strip()
    return (title[:40] or fallback), (body or (text or "").strip())


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


def _synthesize(page_id, kind, subject, force, llm):
    """核心：命中缓存直接返回（0 LLM）；否则检索证据 → LLM 综合 + 命名 → 存盘入表。"""
    ensure_scaffold()
    if not force:
        cached = index_map().get(page_id)
        if cached:
            # wiki-cached-indexed-true：按实回填 indexed（该页是否真在检索表里），不再恒 True。
            m = dict(cached); m["cached"] = True; m["indexed"] = is_indexed(page_id)
            return m
    ctx, srcs = _gather_evidence(subject, llm.get("topk", 8))
    base, model, key = _resolve_llm(llm)
    messages = [{"role": "system", "content": SYN_SYS.format(subject=subject, ctx=ctx)},
                {"role": "user", "content": f"请就「{subject}」写这篇综述。"}]
    text = L.chat_once(messages, base, key, model)
    title, body = _parse_title(text, subject)
    meta = _persist_page(page_id, kind, title, subject, body, srcs, generated_by=model)
    meta["cached"] = False
    return meta


def synthesize_concept(concept, force=False, **llm):
    concept = (concept or "").strip()
    if not concept:
        raise ValueError("概念名为空")
    return _synthesize(f"concept-{_slug(concept)}", "concept", concept, force, llm)


def synthesize_topic(topic_id, force=False, **llm):
    ait = _load_ai_topics()
    t = next((x for x in ait.get("topics", []) if int(x.get("id", -1)) == int(topic_id)), None)
    if not t:
        raise ValueError(f"无此 AI 主题 id={topic_id}（请先建 AI 主题聚类）")
    subject = t.get("name") or f"主题{topic_id}"
    return _synthesize(f"topic-{topic_id}", "topic", subject, force, llm)


def regenerate(page_id, **llm):
    """重生某综合页（覆盖、更新 generated_at）。answer 页无原始检索种子，需回对话重问。"""
    meta = index_map().get(page_id)
    if not meta:
        raise ValueError(f"无此综合页 {page_id}")
    kind = meta.get("kind")
    if kind == "answer":
        raise ValueError("answer 页由对话生成，请在对话里重新提问后再保存")
    subject = meta.get("subject") or meta.get("query") or meta.get("title")
    return _synthesize(page_id, kind, subject, True, llm)


# ═══ 对外：列表 / 取单页 ═══════════════════════════════════════════
def list_pages():
    idx = load_index()
    return [{"id": p["id"], "kind": p.get("kind", "answer"), "title": p.get("title", ""),
             "generated_at": p.get("generated_at", ""), "generated_by": p.get("generated_by", ""),
             "stale": bool(p.get("stale")), "by_agent": bool(p.get("by_agent")),
             "n_sources": len(p.get("sources", []))}
            for p in sorted(idx.get("pages", []),
                            key=lambda x: x.get("generated_at", ""), reverse=True)]


def get_page(page_id):
    meta = index_map().get(page_id)
    if not meta:
        return None
    path = page_path(page_id, meta.get("kind", "answer"))
    md = path.read_text(encoding="utf-8") if path.exists() else ""
    src_cites = []
    for s in meta.get("sources", []):
        key = s.get("key") if isinstance(s, dict) else s
        cite = s.get("citation") if isinstance(s, dict) else ""
        src_cites.append({"key": key, "citation": cite or _resolve_citation(key)})
    return {"id": page_id, "kind": meta.get("kind"), "title": meta.get("title", ""),
            "generated_at": meta.get("generated_at", ""), "generated_by": meta.get("generated_by", ""),
            "stale": bool(meta.get("stale")), "by_agent": bool(meta.get("by_agent")),
            "links": meta.get("links", []), "sources": src_cites, "markdown": md}


# ═══ 对外：一键"不保存"——丢弃某页（§6.4 opt-out；仅人用，不给 agent）═══════
def delete_page(page_id):
    """删三处：data/wiki/**/<id>.md 文件 + index.json 条目（含重建 by_source）+ LanceDB wiki 行。
    幂等：缺哪处删哪处。返回 {deleted, md, table}。**只应由 UI/HTTP 触发，绝不暴露为 MCP 工具。**"""
    idx = load_index()
    meta = next((p for p in idx.get("pages", []) if p.get("id") == page_id), None)

    md_removed = False
    for k in ("answer", "concept", "topic", "digest", "outline"):   # 跨目录兜底删 md
        p = page_path(page_id, k)
        if p.exists():
            try:
                p.unlink(); md_removed = True
            except Exception as e:
                print(f"[wiki] 删 md 失败 {p}：{e}", file=sys.stderr, flush=True)

    if meta:                                          # 删 index 条目并重建 by_source 反查
        pages = [p for p in idx.get("pages", []) if p.get("id") != page_id]
        idx["pages"] = pages
        bs = {}
        for p in pages:
            for s in p.get("sources", []):
                key = s.get("key") if isinstance(s, dict) else s
                if key:
                    bs.setdefault(key, [])
                    if p["id"] not in bs[key]:
                        bs[key].append(p["id"])
        idx["by_source"] = bs
        _save_index(idx)

    table_removed = False
    try:
        import retriever as R
        table_removed = R.delete_wiki_page(page_id)   # 删表行 + 内存 records/wiki（幂等）
    except Exception as e:
        print(f"[wiki] 删表行失败：{e}", file=sys.stderr, flush=True)

    return {"deleted": bool(meta) or md_removed or table_removed,
            "md": md_removed, "table": table_removed}
