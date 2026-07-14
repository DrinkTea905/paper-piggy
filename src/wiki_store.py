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
import sys, os, json, time, hashlib, re, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import llm as L


class WikiWriteDenied(PermissionError):
    """agent 试图覆盖人工核验过的页（by_agent=False）时抛出。人可以覆盖 agent 的页，反之不行。"""


# index.json 的读-改-写必须串行：FastAPI 是多线程的，两个并发写会丢更新
# （_atomic_write 只防文件撕裂，不防 read-modify-write 的丢更新）。
_INDEX_LOCK = threading.RLock()

# EN-W6：v1 → v2——新增「新建页 vs 原地编辑」判定规则与正文 [[互链]] 约定。
# 升级机制沿 v0→v1 先例（见 ensure_scaffold / _FACTORY_HASHES）：仅当用户没手改过才自动升级。
SCHEMA_VERSION = "v2"

WIKI_MD_SEED = """# 本地知识库 · 综合层（Wiki）约定 — schema v2

> 本目录（data/wiki/）是"综合层"：把 LLM 对文献的理解**持久化**成可累积、带引用、互链的页面。
> 它是文献库之上的**附加缓存**，不是替代——删除本目录不影响文献库/Zotero/索引。

## 页面种类（kind）
- answer   一次 /chat 问答沉淀下来的综述
- concept  概念页（按需生成 + 缓存）
- topic    主题页（对应 AI 主题聚类的一簇）
- digest   资料汇编（带印刷页引注的综述）
- outline  选题框架 / 三级大纲
- entity   **实体页**：作者、机构、案件、制度——随每次 ingest 增量加厚的骨干节点
- overview **总论页**：随全库演进的 thesis；每读一篇新文献，它被强化或被挑战

## 每页结构
YAML frontmatter（id/kind/title/subject/sources/generated_at/generated_by/stale/by_agent/links）+ markdown 正文。
- sources：本页综合所依据的论文 key（provenance 命脉；每个 key 可回溯到印刷页码）。
- generated_at / generated_by：生成时间与模型（可信度审计）。
- stale：该页已被新文献推翻或不再成立。用 `mark_stale` 置位，检索中乘性重罚。
- by_agent：agent 写回、未经人工核验（界面标 🤖）。
- links：交叉链接到其它 wiki 页 id。用 `set_wiki_links` 维护——**这是把一堆孤岛补成一张图的唯一途径**。
frontmatter 自足：删掉 index.json 也能从各 .md 完整重建。
正文中也可以用 `[[page-id]]` 或 `[[page-id|显示文字]]` 互链到其它 wiki 页；
**链接目标必须真实存在**（lint 的 body_broken_link 会查出指向不存在页的正文互链）。

## 新建页 vs 原地编辑（判定规则）
每次要写回时先做这道判断（经验上这条启发式约 90% 情况给出正确选择，靠的就是把规则写死在本 schema 里）：
- **新建页**：内容是一个可独立成链接目标的**新概念 / 新实体**（人物、机构、案件、制度、学说）——
  它值得被其它页 [[互链]] 指到，就为它建新页（选对 kind：概念用 concept、实体用 entity）。
- **原地编辑**：内容是**既有页**的属性、进展、修正或补充证据——用 update_wiki_page 原地改，
  **保留原页结构**（标题层级、小节顺序），只动需要动的段落，别整页推倒重写。
- **拿不准**：原地 append 到最相关的既有页末尾，并在追加内容开头注明「（新增，待归置：…）」，
  留给人工后续决定要不要拆成独立页。

## 写回纪律（给人与 agent）
1. 每个论断后带 [n] 引用，n 对应"参考来源"里的论文；不臆造、不给无出处的断言。
2. 下判断前先 `read_source` 读原文，不要只凭检索片段。
3. 综合是快照不是定论：读者以原文页码为准；旧综合可能已过时。
4. 只写 data/wiki/；绝不改动文献库、索引、Zotero。你能写、不能删。
5. **不覆盖别人的结论**：写回会被拒绝覆盖人工核验过的页（HTTP 409）。旧页被推翻时用 `mark_stale` 标脏 + 写清理由。
6. 矛盾/争议只作"未核实"的只读提示，绝不落成 wiki 断言。
7. 新增/更新一篇文献后，用 `propose_wiki_updates(key)` 看它影响了哪些页，逐页处理。
   gist 的经验是：**一篇源常常触及 10-15 个页**。只写一页往往说明你漏了。
8. 定期 `lint_wiki` 体检：孤儿页、过时页、断链、该有独立页却没有的概念。

## 检索中的表现
每页作为一行进同一张 LanceDB 表（chunk_id 以 "::wiki" 结尾）参与召回；命中时标注
"（来自既有综合，可能已过时）"。新鲜页同分让位于原始文献；stale 页乘性重罚，沉到真论文之下。
未配 AI 模型时生成的「证据清单」不入检索表。

## 版本历史
每次写入自动记一版（有 git 用 git，无 git 用 .history 快照）。可在应用里查看历史、回滚任意一页。
所以放手改——改错了能退回来。
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: Path, text: str, retries=6):
    """写临时文件再 os.replace 换上去（防写一半崩溃留下半截文件）。

    带退避重试：OneDrive / 杀毒软件会短暂独占锁住目标文件，此时 os.replace 抛
    PermissionError（WinError 5）。这不是真的没权限，等几十毫秒就好。
    不重试的话，一次同步撞车就能让 index.json 写失败、整个写回操作报错。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last = None
    for i in range(retries):
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return
        except OSError as e:                 # PermissionError 是 OSError 的子类
            last = e
            time.sleep(0.04 * (2 ** i))      # 40ms → 1.28s，总计约 2.5s
    try:
        tmp.unlink(missing_ok=True)          # 别留下半截 .tmp
    except Exception:
        pass
    raise last


# 页种 → 目录。加 entity（实体页）/ overview（演进中的总论）——gist 反复点名却一直缺席的两个骨干页种。
# 所有遍历 kind 的地方都用 KINDS，别再散落多份字符串元组（曾漏掉新 kind 导致删除/重建时遗漏文件）。
KIND_DIRS = {
    "answer":   lambda: C.WIKI_ANSWERS_DIR,
    "concept":  lambda: C.WIKI_CONCEPTS_DIR,
    "topic":    lambda: C.WIKI_TOPICS_DIR,
    "digest":   lambda: C.WIKI_DIGEST_DIR,
    "outline":  lambda: C.WIKI_OUTLINE_DIR,
    "entity":   lambda: C.WIKI_ENTITY_DIR,
    "overview": lambda: C.WIKI_OVERVIEW_DIR,
}
KINDS = tuple(KIND_DIRS)


def ensure_scaffold():
    """建目录 + 保证 WIKI.md 存在且不过期。

    WIKI.md 是 gist 的第 3 层 schema，会被 MCP initialize 整篇下发给 agent —— 它过期，
    agent 就照着旧规约干活（比如不知道有 entity 页、不知道该 mark_stale 而不是覆盖）。
    但用户可能手改过它，所以只在**内容仍是我们某个已知旧版原样**时才升级；
    一旦发现被改动过，就保留用户版本，只提示一句。"""
    for d in [C.WIKI_DIR] + [f() for f in KIND_DIRS.values()]:
        d.mkdir(parents=True, exist_ok=True)
    if not C.WIKI_SCHEMA_MD.exists():
        _atomic_write(C.WIKI_SCHEMA_MD, WIKI_MD_SEED)
        return
    try:
        cur = C.WIKI_SCHEMA_MD.read_text(encoding="utf-8")
    except Exception:
        return
    if f"schema {SCHEMA_VERSION}" in cur:
        return
    if _looks_untouched(cur):
        # EN-W6：留档名按旧版内容里的版本号命名（v1 起标题带 "schema vN"；探不到的老文件按 v0 处理），
        # 别再写死 WIKI.v0.md——否则 v1→v2 升级会把 v1 存进 v0 的档。
        m = re.search(r"schema (v\d+)", cur)
        old_ver = m.group(1) if m else "v0"
        _atomic_write(C.WIKI_DIR / f"WIKI.{old_ver}.md", cur)      # 留档
        _atomic_write(C.WIKI_SCHEMA_MD, WIKI_MD_SEED)
        print(f"[wiki] WIKI.md 已升级到 schema {SCHEMA_VERSION}（旧版留档为 WIKI.{old_ver}.md）",
              file=sys.stderr, flush=True)
        try:
            import wiki_vcs as V
            V.commit(f"WIKI.md 升级到 schema {SCHEMA_VERSION}")
        except Exception:
            pass
    else:
        print("[wiki] WIKI.md 似乎被手工改过，保留你的版本（schema v2 新增的「新建页 vs 原地编辑」"
              "判定规则与正文 [[互链]] 约定需要你自行补进规约）", file=sys.stderr, flush=True)


# 各历史版本出厂 WIKI.md 的 normalized（去所有空白）sha1。
# 只有内容与某个出厂版**一字不差**时才自动升级。
# 不能靠"含有某几个特征串"来判断——用户在文件末尾追加自己的规矩后，特征串依然都在，
# 那样会把他写的东西直接覆盖掉。
_FACTORY_HASHES = {
    "2d7c7749b165d5640772d62791c6f9e569aa5e47",   # schema v0
    "21793476a7a6538582a3d14eb0651f426d4b45a6",   # schema v1（EN-W6：升 v2 时对 v1 出厂原样放行自动升级）
}


def _norm_hash(text):
    return hashlib.sha1(re.sub(r"\s+", "", text or "").encode("utf-8")).hexdigest()


def _looks_untouched(text):
    """WIKI.md 是否仍是某个出厂原样（没被用户改过一个字）。"""
    return _norm_hash(text) in _FACTORY_HASHES


# ═══ index.json（页元数据缓存；.md 的 frontmatter 才是可重建的事实来源）════════
def _parse_frontmatter(text):
    """解析本模块自己写出的 frontmatter（固定 `key: value`，值为 JSON 或裸串）。不依赖 pyyaml。"""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    meta = {}
    for ln in text[3:end].strip().splitlines():
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        k, v = k.strip(), v.strip()
        try:
            meta[k] = json.loads(v)      # title/sources/links/stale/by_agent 都是合法 JSON
        except Exception:
            meta[k] = v                  # id/kind/generated_at/generated_by 是裸串
    return meta or None


def _build_by_source(pages):
    """反查：某论文 key → 引用它的 wiki 页 id 列表（供 stale 传播 / backlinks）。"""
    bs = {}
    for p in pages:
        for s in p.get("sources", []):
            key = s.get("key") if isinstance(s, dict) else s
            if key:
                bs.setdefault(key, [])
                if p["id"] not in bs[key]:
                    bs[key].append(p["id"])
    return bs


def _rebuild_index_from_disk():
    """从各 kind 目录的 .md frontmatter 重建 index.json。
       旧行为是解析失败就静默返回空表，而紧接着的任何一次保存都会把这张空表写回盘
       —— 所有旧页的元数据被永久裁掉。宁可慢，也不能丢。"""
    pages = []
    for kind in KINDS:
        d = kind_dir(kind)
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                fm = _parse_frontmatter(f.read_text(encoding="utf-8"))
            except Exception:
                fm = None
            if not fm or not fm.get("id"):
                continue
            pages.append({
                "id": fm["id"], "kind": fm.get("kind", kind), "title": fm.get("title", ""),
                "subject": fm.get("subject", "") or fm.get("title", ""),
                "sources": [{"key": k, "citation": _resolve_citation(k)}
                            for k in (fm.get("sources") or [])],
                "generated_at": fm.get("generated_at", ""),
                "generated_by": fm.get("generated_by", ""),
                "verified_at": fm.get("verified_at", "") or "",   # W3：核验章随 .md 重建，不因 index 损坏而丢
                "stale": bool(fm.get("stale")), "by_agent": bool(fm.get("by_agent")),
                "links": fm.get("links") or [],
            })
    print(f"[wiki] index.json 已从 {len(pages)} 个 .md 重建", file=sys.stderr, flush=True)
    idx = {"pages": pages, "by_source": _build_by_source(pages), "updated_at": _now()}
    try:
        _save_index(idx)     # 立刻落盘：否则此后每次 load_index 都要重新报损坏并全盘重扫
    except Exception as e:
        print(f"[wiki] 重建结果落盘失败（仅本次内存可用）：{e}", file=sys.stderr, flush=True)
    return idx


def load_index():
    """损坏/缺失时**不**返回空表（那等于授权下一次保存清空全库），而是从 .md 扫盘重建。"""
    if C.WIKI_INDEX.exists():
        raw = None
        try:
            raw = C.WIKI_INDEX.read_text(encoding="utf-8")
            idx = json.loads(raw)
            if isinstance(idx, dict) and isinstance(idx.get("pages"), list):
                return idx
            raise ValueError("结构异常：pages 不是列表")
        except Exception as e:
            print(f"[wiki] index.json 损坏（{e}），改从 .md 重建", file=sys.stderr, flush=True)
            if raw is not None:                     # 坏文件留档，便于事后查证
                try:
                    _atomic_write(C.WIKI_DIR / f"index.corrupt-{int(time.time())}.json", raw)
                except Exception:
                    pass
            return _rebuild_index_from_disk()
    return _rebuild_index_from_disk()               # 不存在也扫盘：可能只是 index.json 被误删


def _save_index(idx):
    with _INDEX_LOCK:
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
    with _INDEX_LOCK:                     # 读-改-写必须串行，否则并发保存会丢页
        idx = load_index()
        pages = [p for p in idx.get("pages", []) if p.get("id") != meta["id"]]
        # W3：verified_at 一并入表；正文被重写(_persist_page)时 meta 无此键 → 置空=核验自然失效
        entry = {k: meta.get(k) for k in
                 ("id", "kind", "title", "subject", "sources", "generated_at", "generated_by",
                  "verified_at", "stale", "by_agent", "links")}
        pages.append(entry)
        idx["pages"] = pages
        idx["by_source"] = _build_by_source(pages)
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
    f = KIND_DIRS.get(kind)
    return f() if f else C.WIKI_ANSWERS_DIR


def page_path(page_id, kind):
    return kind_dir(kind) / f"{page_id}.md"


# ═══ 页面渲染（frontmatter + markdown；不依赖 pyyaml）═══════════════
def _frontmatter(meta):
    def jl(items):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in items) + "]"
    src_keys = [(s.get("key") if isinstance(s, dict) else s) for s in meta.get("sources", [])]
    # subject / by_agent 也落盘：.md 必须自足到能重建 index.json（见 _rebuild_index_from_disk）
    return "\n".join([
        "---",
        f"id: {meta['id']}",
        f"kind: {meta['kind']}",
        f"title: {json.dumps(meta.get('title', ''), ensure_ascii=False)}",
        f"subject: {json.dumps(meta.get('subject', '') or '', ensure_ascii=False)}",
        f"sources: {jl([k for k in src_keys if k])}",
        f"generated_at: {meta.get('generated_at', '')}",
        f"generated_by: {meta.get('generated_by', '')}",
        f"stale: {'true' if meta.get('stale') else 'false'}",
        f"by_agent: {'true' if meta.get('by_agent') else 'false'}",
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
    gb = meta.get("generated_by", "") or ""
    if is_degraded(gb):
        body += ["", f"*（⚠ {degraded_reason(gb)}。生成于 {meta.get('generated_at', '')}，"
                 f"基于 {len(sources)} 篇。请以原文为准。）*"]
    else:
        body += ["", f"*（本页为本地综合，生成于 {meta.get('generated_at', '')} · 基于 "
                 f"{len(sources)} 篇 · 模型 {gb or '未知'}；可能已过时，请以原文为准。）*"]
    return "\n".join(body)


def _plain_body(answer, sources):
    """给 reranker/嵌入用的纯文本（答案 + 来源引用），比 markdown 更利于语义匹配。"""
    src = " ".join(s.get("citation", "") for s in sources)
    return (answer + "\n" + src).strip()


def _snapshot(page_id, message=""):
    """记一版历史（git 或快照兜底）。永不抛异常——版本历史是安全网，不是写入路径上的关卡。"""
    try:
        import wiki_vcs as V
        meta = index_map().get(page_id)
        if not meta:
            return None
        p = page_path(page_id, meta.get("kind", "answer"))
        return V.snapshot(page_id, p, message)
    except Exception as e:
        print(f"[wiki] 记录版本失败（不影响写入）{page_id}：{e}", file=sys.stderr, flush=True)
        return None


def page_history(page_id, limit=30):
    import wiki_vcs as V
    meta = index_map().get(page_id)
    if not meta:
        raise ValueError(f"无此综合页 {page_id}")
    return {"id": page_id, "title": meta.get("title", ""), "backend": V.backend(),
            "versions": V.history(page_id, kind_dir(meta.get("kind", "answer")).name, limit)}


def restore_page(page_id, rev):
    """把某页回滚到历史版本：取回旧 .md → 重写盘 → 重建 index 条目 → 重新入表。
       回滚本身也会记一版（可再回滚回去）。"""
    import wiki_vcs as V
    with _INDEX_LOCK:
        meta = index_map().get(page_id)
        if not meta:
            raise ValueError(f"无此综合页 {page_id}")
        kind = meta.get("kind", "answer")
        old_md = V.read_at(page_id, rev, kind_dir(kind).name)
        fm = _parse_frontmatter(old_md)
        if not fm or fm.get("id") != page_id:
            raise ValueError(f"版本 {rev} 的内容不是这一页（frontmatter 对不上）")
        _atomic_write(page_path(page_id, kind), old_md)

        entry = {
            "id": page_id, "kind": fm.get("kind", kind), "title": fm.get("title", ""),
            "subject": fm.get("subject", "") or fm.get("title", ""),
            "sources": [{"key": k, "citation": _resolve_citation(k)} for k in (fm.get("sources") or [])],
            "generated_at": fm.get("generated_at", ""), "generated_by": fm.get("generated_by", ""),
            "verified_at": fm.get("verified_at", "") or "",   # W3：回滚版本里若有核验章则一并恢复
            "stale": bool(fm.get("stale")), "by_agent": bool(fm.get("by_agent")),
            "links": fm.get("links") or [],
        }
        _upsert_index(entry)

    body = re.sub(r"^---[\s\S]*?\n---\n?", "", old_md).strip()
    if not is_degraded(entry["generated_by"]):
        try:
            import retriever as R
            R.index_wiki_page(page_id, entry["title"], body, entry)
        except Exception as e:
            print(f"[wiki] 回滚后重新入表失败：{e}", file=sys.stderr, flush=True)
    _snapshot(page_id, f"回滚到 {rev}")
    return {"id": page_id, "restored_from": rev, "title": entry["title"]}


def is_degraded(generated_by):
    """降级产物判定：未配 key / LLM 调用失败 / 库内无命中。

    这类页的正文**不是综合**，而是原文片段清单或占位提示（见 research_assistant._fallback_body）。
    它们绝不能进检索表与真论文同台竞争——否则用户搜到的"综述"其实是自己文献的片段复读，
    还带着 wiki 徽标冒充综合。成功的 LLM 综述 generated_by=模型名。
    （generated_by 为空不算降级：网页手工保存答案时可能不带模型名。）"""
    gb = (generated_by or "").strip()
    return gb.startswith("fallback(") or gb in ("no-hits", "no-key")


def degraded_reason(generated_by):
    """给人看的降级原因（法学研究者读得懂的话，不是 "fallback(no-key)" 这种黑话）。"""
    gb = (generated_by or "").strip()
    if gb in ("no-key", "fallback(no-key)"):
        return "未配置 AI 模型，本页只是库内原文片段的清单，不是 AI 写的综述"
    if gb == "no-hits":
        return "知识库里没有相关文献，本页没有实质内容"
    if gb.startswith("fallback("):
        return "AI 生成失败，本页退化成了库内原文片段的清单，不是 AI 写的综述"
    return ""


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


# ═══ EN-W4：来源相关性门槛——低相关命中不配进 provenance ═══════════════
# 真实案例：digest-92820857 的 sources 混入了测试文件和离题文献——检索召回的长尾命中
# 被原样落成「本页所依据的论文」，读者据此回溯就会扑空。落盘前按相对分过滤。
def _src_score_margin():
    """相对最高分的容差。用**相对差**而非绝对阈值，是因为两种 reranker 分数尺度天差地别：
    本地 bge-reranker 输出 0~10+ 的 logit（史称 wiki 降权“纸糊”教训——别拿 0.05 去挡 10 分），
    API（SiliconFlow）重排返回 0~1 的归一分。绝对阈值只能对一端有效，相对差两端通吃。
    数值经 getattr 从 config 取（config.py 归 Worker 4，不在里面加字段，这里给默认值）。"""
    try:
        import settings as S
        if S.is_api():
            return float(getattr(C, "WIKI_SRC_SCORE_MARGIN_API", 0.3))
    except Exception:
        pass
    return float(getattr(C, "WIKI_SRC_SCORE_MARGIN", 3.0))


def filter_provenance(hits, min_keep=2):
    """按「相对最高分」过滤：保留 score >= best - margin 的条目。
    - 只有带数值 score 的 dict 参与比较；不带 score 的条目（纯 key 字符串、老调用方）原样保留——
      无从判断相关性时宁可放行，也别把真来源滤掉。
    - **至少保留 min_keep(=2) 条**带分条目：全都很差时别把 provenance 清空——
      空 provenance 比弱 provenance 更糟（lint 会把它报成 no_sources，页也失去可回溯性）。"""
    hits = list(hits or [])
    scored = [(i, float(h.get("score"))) for i, h in enumerate(hits)
              if isinstance(h, dict) and isinstance(h.get("score"), (int, float))]
    if len(scored) <= min_keep:
        return hits
    best = max(sc for _, sc in scored)
    keep = {i for i, sc in scored if sc >= best - _src_score_margin()}
    if len(keep) < min_keep:               # 门槛卡得只剩 1 条时，放行分数最高的前 min_keep 条
        keep = {i for i, _ in sorted(scored, key=lambda t: -t[1])[:min_keep]}
    scored_idx = {i for i, _ in scored}
    return [h for i, h in enumerate(hits) if i not in scored_idx or i in keep]


def _norm_sources(sources):
    """规整 sources：去重 key，服务端权威解析页级引用（客户端 citation 作兜底）。
    EN-W4：先过相关性门槛——answer/digest 等页沉淀时，调用方若在 sources 里带了检索 score，
    低于「最高分 - margin」的离题命中在此被剔除，不落成 provenance。"""
    seen, out = set(), []
    for s in filter_provenance(sources):
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
    by_agent=True 标记"agent 经 MCP 写回、未经人工核验"（前端标 🤖 徽章，供事后一键剔除）。

    写权护栏：page_id 由标题/主题哈希而来，同标题=同 id=覆盖。若不设防，agent 只要用一个
    与你已核验页相同的标题调 save_synthesis，就会静默抹掉那一页。人可以覆盖 agent 的页
    （人有最终权威），agent 不能覆盖人的页。"""
    with _INDEX_LOCK:
        existing = index_map().get(page_id)
        if existing and by_agent and not existing.get("by_agent"):
            raise WikiWriteDenied(
                f"「{existing.get('title') or page_id}」是人工保存/核验过的综合页，agent 不得覆盖。"
                f"请换一个标题，或先用 get_wiki_page({page_id}) 读它再决定。")
        # 已人工核验（verified_at）的页——即便原是 agent 页——同样不许 agent 覆盖；
        # 发现被新文献推翻应 mark_stale 标脏写理由，而非抹掉核验结论（护栏此前只挡「人写的页」，漏了「人核验过的 agent 页」）。
        if existing and by_agent and existing.get("verified_at"):
            raise WikiWriteDenied(
                f"「{existing.get('title') or page_id}」已经人工核验，agent 不得覆盖。"
                f"若它被新文献推翻，请用 mark_stale 标脏并写清理由。")
        meta = {
            "id": page_id, "kind": kind,
            "title": (title or _title_from(subject, body)).strip(),
            "subject": subject, "sources": norm_sources,
            "generated_at": _now(), "generated_by": generated_by or "",
            "stale": False, "by_agent": bool(by_agent),
            # 重生/覆盖时保留既有互链，别把 links 清零（波次2 set_wiki_links 会写它）
            "links": list((existing or {}).get("links") or []),
            "query": subject,
        }
        # 写盘与 index 更新必须同在锁内，否则护栏检查与落盘之间存在 TOCTOU 窗口
        _atomic_write(page_path(page_id, kind), _render_md(meta, subject, body, norm_sources))
        _upsert_index(meta)          # RLock 可重入
    indexed, degraded = False, is_degraded(meta["generated_by"])
    if degraded:
        # 降级页只存盘、可在综述库阅读，但不进检索表（否则片段清单会冒充综合污染召回）
        print(f"[wiki] 降级页 {page_id}（{meta['generated_by']}）仅存盘、不入检索表",
              file=sys.stderr, flush=True)
    else:
        try:
            import retriever as R
            indexed = R.index_wiki_page(page_id, meta["title"], _plain_body(body, norm_sources), meta)
        except Exception as e:
            print(f"[wiki] 入表失败（仅存盘，重建索引后可检索）：{e}", file=sys.stderr, flush=True)
    meta["indexed"] = bool(indexed)
    meta["degraded"] = degraded
    _snapshot(page_id, f"{'agent' if by_agent else '人'}写入 {kind} 页")
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
    # EN-W4：ctx 与 srcs 必须用**同一份**过滤后的列表——LLM 按 ctx 的 [n] 写引用，
    # 页面「参考来源」再从 srcs 编号；两边名单不一致会出现「正文引了 [n]、来源里没有 n」，
    # 恰好毁掉 provenance 的可回溯性。离题长尾片段 LLM 少看几条无损失。
    hits = filter_provenance(hits)
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
             "verified_at": p.get("verified_at") or "",   # W3：人工核验时间（无则空串）
             "stale": bool(p.get("stale")), "by_agent": bool(p.get("by_agent")),
             "degraded": is_degraded(p.get("generated_by", "")),
             "degraded_reason": degraded_reason(p.get("generated_by", "")),
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
            "verified_at": meta.get("verified_at") or "",   # W3：人工核验时间（无则空串）
            "stale": bool(meta.get("stale")), "by_agent": bool(meta.get("by_agent")),
            "degraded": is_degraded(meta.get("generated_by", "")),
            "degraded_reason": degraded_reason(meta.get("generated_by", "")),
            "links": meta.get("links", []), "sources": src_cites, "markdown": md}


def reindex_missing_pages():
    """存量回灌：把「非降级、但不在检索表」的综合页补嵌回检索表，兑现『重建索引后可检索』的承诺。
       触发场景：① light 模式下保存过的页；② full 模式嵌入曾失败（如 API 余额0→403）的页；
       ③ 全量重建表(overwrite)把已入表的 wiki 行一并冲掉后——它们在任何重建/重载路径里都不会被自动补回。
       只在 full 模式（检索表已载入）下有意义，由 retriever._load_wiki_index 在表载入后调用。返回回灌页数。"""
    try:
        import retriever as R
    except Exception:
        return 0
    if "tbl" not in R.M or R.M.get("records") is None:
        return 0
    n = 0
    for pid, meta in list(index_map().items()):
        if is_degraded(meta.get("generated_by", "")):
            continue                                   # 降级页按设计不入表
        if f"{pid}::wiki" in R.M["records"]:
            continue                                   # 已在表内
        try:
            pg = get_page(pid)
            if not pg:
                continue
            if R.index_wiki_page(pid, meta.get("title", ""),
                                 _plain_body(pg.get("markdown", ""), meta.get("sources") or []), meta):
                n += 1
        except Exception as e:
            print(f"[wiki] 回灌综合页 {pid} 失败（跳过）：{e}", file=sys.stderr, flush=True)
    if n:
        print(f"[wiki] 回灌 {n} 个未入表的综合页到检索表", file=sys.stderr, flush=True)
    return n


# ═══ 对外：stale 写侧（此前只有消费端：检索降权 / UI 徽章 / config 惩罚值，
#     却没有任何代码把 stale 置 True——一台永不触发的死机关）═══════════════
def set_stale(page_id, stale=True, reason=""):
    """标记/清除某页「已过时」。三处同步：index.json + .md frontmatter + 检索期内存 M["wiki"]。
       不改内存的话，降权要等下次重启才生效。"""
    with _INDEX_LOCK:
        idx = load_index()
        meta = next((p for p in idx.get("pages", []) if p.get("id") == page_id), None)
        if not meta:
            raise ValueError(f"无此综合页 {page_id}")
        meta["stale"] = bool(stale)
        if reason:
            meta["stale_reason"] = reason
        elif "stale_reason" in meta:
            meta.pop("stale_reason")
        _save_index(idx)

        # 同步 .md 的 frontmatter（.md 是可重建的事实来源，不能与 index.json 说法不一）
        path = page_path(page_id, meta.get("kind", "answer"))
        if path.exists():
            try:
                txt = path.read_text(encoding="utf-8")
                new = re.sub(r"(?m)^stale: (?:true|false)$",
                             f"stale: {'true' if stale else 'false'}", txt, count=1)
                if new != txt:
                    _atomic_write(path, new)
            except Exception as e:
                print(f"[wiki] 同步 .md stale 失败 {page_id}：{e}", file=sys.stderr, flush=True)

    # 让检索期降权立刻生效（M["wiki"] 是 _wiki_penalty 的数据源）
    try:
        import retriever as R
        if page_id in (R.M.get("wiki") or {}):
            R.M["wiki"][page_id]["stale"] = bool(stale)
    except Exception:
        pass
    return {"id": page_id, "stale": bool(stale), "reason": reason,
            "title": meta.get("title", ""), "kind": meta.get("kind")}


def set_verified(page_id):
    """W3：人工核验盖章——写 verified_at 时间戳，三处同步（index.json + .md frontmatter +
       检索期内存 M["wiki"]，先例见 set_stale）。核验是人的动作，**不做成 MCP 工具**：
       agent 不得给自己的产出盖「已核验」章（那会架空 by_agent 写权护栏）。"""
    ts = _now()
    with _INDEX_LOCK:
        idx = load_index()
        meta = next((p for p in idx.get("pages", []) if p.get("id") == page_id), None)
        if not meta:
            raise ValueError(f"无此综合页 {page_id}")
        meta["verified_at"] = ts
        _save_index(idx)

        # 同步 .md frontmatter（只动 frontmatter 区，避免误伤正文里的 --- 分隔线）
        path = page_path(page_id, meta.get("kind", "answer"))
        if path.exists():
            try:
                txt = path.read_text(encoding="utf-8")
                end = txt.find("\n---", 3) if txt.startswith("---") else -1
                if end >= 0:
                    head = txt[:end]
                    if re.search(r"(?m)^verified_at:", head):
                        head = re.sub(r"(?m)^verified_at:.*$", f"verified_at: {ts}", head, count=1)
                        new = head + txt[end:]
                    else:
                        new = head + f"\nverified_at: {ts}" + txt[end:]
                    if new != txt:
                        _atomic_write(path, new)
            except Exception as e:
                print(f"[wiki] 同步 .md verified_at 失败 {page_id}：{e}", file=sys.stderr, flush=True)

    # 同步检索期内存（供前端徽章/后续按核验态调权直接生效，无需重启）
    try:
        import retriever as R
        if page_id in (R.M.get("wiki") or {}):
            R.M["wiki"][page_id]["verified_at"] = ts
    except Exception:
        pass
    return {"id": page_id, "verified_at": ts,
            "title": meta.get("title", ""), "kind": meta.get("kind")}


def set_links(page_id, links, mode="replace", by_agent=False):
    """写 links —— gist 的 cross-references。此前 links 恒为 []，wiki 是一堆孤岛而非图。

    mode: replace=整体替换 / add=并入 / remove=移除。
    只接受**已存在**的页 id（拒绝断链），自动去重、剔除自链。返回 {links, skipped}。
    by_agent 只用于版本历史/时间线标注（谁动的图），不影响写入行为。"""
    want = [str(x).strip() for x in (links or []) if str(x).strip()]
    with _INDEX_LOCK:
        idx = load_index()
        by_id = {p["id"]: p for p in idx.get("pages", [])}
        meta = by_id.get(page_id)
        if not meta:
            raise ValueError(f"无此综合页 {page_id}")

        skipped = [x for x in want if x == page_id or x not in by_id]
        valid = [x for x in want if x != page_id and x in by_id]

        cur = list(meta.get("links") or [])
        if mode == "add":
            new = cur + [x for x in valid if x not in cur]
        elif mode == "remove":
            new = [x for x in cur if x not in valid]
        else:
            new = valid
        seen, out = set(), []
        for x in new:                      # 去重保序
            if x not in seen:
                seen.add(x); out.append(x)
        meta["links"] = out
        _save_index(idx)

        # 同步 .md 的 frontmatter（.md 必须自足到能重建 index.json）
        path = page_path(page_id, meta.get("kind", "answer"))
        if path.exists():
            try:
                txt = path.read_text(encoding="utf-8")
                jl = "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in out) + "]"
                new_txt = re.sub(r"(?m)^links: .*$", f"links: {jl}", txt, count=1)
                if new_txt != txt:
                    _atomic_write(path, new_txt)
            except Exception as e:
                print(f"[wiki] 同步 .md links 失败 {page_id}：{e}", file=sys.stderr, flush=True)

    try:                                   # 让 UI/检索期元数据即时反映
        import retriever as R
        if page_id in (R.M.get("wiki") or {}):
            R.M["wiki"][page_id]["links"] = out
    except Exception:
        pass
    # 时间线按动作前缀「agent」判 by_agent（wiki_vcs.log_events）——机器改动是最该被复核的，别标成人
    _snapshot(page_id, f"{'agent' if by_agent else '人'}调整互链({mode})")
    return {"id": page_id, "links": out, "skipped": skipped}


def update_page(page_id, kind=None, title=None, content=None, sources=None,
                mode="replace", links=None, generated_by="", by_agent=False):
    """建 / 覆盖 / 追加任意 kind 的 wiki 页 —— gist 里 LLM「creates pages, updates them」的动词。

    - 页不存在：按给定 kind 新建（kind 必填）。
    - 页已存在：mode=replace 覆盖正文；mode=append 追加到正文末尾（保留原有内容与来源）。
    - 沿用 _persist_page 的写权护栏：agent 不能覆盖人工核验过的页。
    - sources 合并去重；links 可一并写入。
    """
    ensure_scaffold()
    body = (content or "").strip()
    if not body:
        raise ValueError("空正文，拒绝写入")

    with _INDEX_LOCK:
        existing = index_map().get(page_id)
        if existing:
            kind = kind or existing.get("kind")
            if mode == "append":
                old = get_page(page_id) or {}
                old_body = re.sub(r"^---[\s\S]*?\n---\n?", "", old.get("markdown", "")).strip()
                # 去掉旧正文尾部的「参考来源」区与生成落款，避免层层堆叠
                old_body = re.split(r"\n---\n\s*\*\*参考来源\*\*", old_body)[0].strip()
                old_body = re.sub(r"(?m)^\*（.*）\*\s*$", "", old_body).strip()
                body = (old_body + "\n\n" + body).strip()
            merged = list(existing.get("sources") or []) + list(sources or [])
        else:
            if not kind:
                raise ValueError("新建页必须指定 kind：" + "/".join(KINDS))
            merged = list(sources or [])
        if kind not in KINDS:
            raise ValueError(f"未知 kind={kind}，可选：{'/'.join(KINDS)}")
        subject = (existing or {}).get("subject") or title or page_id

    meta = _persist_page(page_id, kind, title or (existing or {}).get("title", ""),
                         subject, body, _norm_sources(merged), generated_by, by_agent)
    if links is not None:
        try:
            meta["links"] = set_links(page_id, links, mode="replace")["links"]
        except Exception as e:
            print(f"[wiki] 写 links 失败 {page_id}：{e}", file=sys.stderr, flush=True)
    _snapshot(page_id, f"{'agent' if by_agent else '人'}修订正文({mode})")   # 前缀供时间线判 by_agent
    return meta


def backlinks(key=None, page_id=None):
    """反查。by_source 表建好落盘却一直零消费者，这是它的第一个出口。
       - key=论文 key   → 哪些综合页引用了这篇论文（stale 传播 / lint 的地基）
       - page_id=页 id  → 该页引用了哪些论文(sources)，以及哪些页 links 指向它(inbound)
    """
    idx = load_index()
    pages = idx.get("pages", [])
    by_id = {p["id"]: p for p in pages}

    if key:
        ids = (idx.get("by_source") or {}).get(key, [])
        return {"key": key, "cited_by": [
            {"id": i, "title": by_id.get(i, {}).get("title", ""),
             "kind": by_id.get(i, {}).get("kind", ""),
             "stale": bool(by_id.get(i, {}).get("stale"))}
            for i in ids if i in by_id]}

    if page_id:
        p = by_id.get(page_id)
        if not p:
            raise ValueError(f"无此综合页 {page_id}")
        inbound = [{"id": q["id"], "title": q.get("title", "")}
                   for q in pages if page_id in (q.get("links") or [])]
        outbound = [{"id": i, "title": by_id.get(i, {}).get("title", "")}
                    for i in (p.get("links") or []) if i in by_id]
        return {"page_id": page_id, "title": p.get("title", ""),
                "sources": p.get("sources", []),
                "links_out": outbound, "links_in": inbound,
                "orphan": not inbound and not outbound}

    raise ValueError("需要 key 或 page_id 之一")


def graph():
    """给前端图视图的邻接数据：节点=wiki 页，边=links。孤儿页标出来。
       用户不装 Obsidian，所以这张图要长在应用自己的界面里。"""
    idx = load_index()
    pages = idx.get("pages", [])
    ids = {p["id"] for p in pages}
    edges, deg = [], {p["id"]: 0 for p in pages}
    for p in pages:
        for t in (p.get("links") or []):
            if t in ids:
                edges.append({"source": p["id"], "target": t})
                deg[p["id"]] += 1
                deg[t] += 1
    nodes = [{"id": p["id"], "title": p.get("title", ""), "kind": p.get("kind", "answer"),
              "stale": bool(p.get("stale")), "by_agent": bool(p.get("by_agent")),
              "degraded": is_degraded(p.get("generated_by", "")),
              "n_sources": len(p.get("sources") or []), "degree": deg[p["id"]],
              "orphan": deg[p["id"]] == 0}
             for p in pages]
    return {"nodes": nodes, "edges": edges,
            "n_orphan": sum(1 for n in nodes if n["orphan"])}


def propose_updates(source_key, topk=12):
    """gist 的核心命题「a single source might touch 10-15 wiki pages」——变成一次可执行调用。

    给一篇论文 key，回答：**它影响了哪些 wiki 页、每页该怎么改**。两条线索：
      1) 直接引用：by_source 反查（这张表建好落盘却一直零消费者）。这些页的结论可能被这篇推翻。
      2) 主题相关但未引用：拿这篇的标题/摘要去检索既有 wiki 页，找出讲同一件事却没引它的页。
         这类页是「该更新却没人知道」的重灾区——正是 gist 说 LLM 该替人做的 bookkeeping。

    只**建议**，不动手。agent 拿到后逐页决定 update_wiki_page / mark_stale / set_wiki_links。
    """
    idx = load_index()
    pages = idx.get("pages", [])
    by_id = {p["id"]: p for p in pages}
    if not pages:
        return {"key": source_key, "n_affected": 0, "affected": [],
                "note": "综合层还没有任何页——这篇文献可以成为第一页的素材。"}

    cited_ids = set((idx.get("by_source") or {}).get(source_key, []))
    affected = []
    for pid in cited_ids:
        p = by_id.get(pid)
        if not p:
            continue
        affected.append({
            "id": pid, "title": p.get("title", ""), "kind": p.get("kind", ""),
            "relation": "cites_this_source",
            "stale": bool(p.get("stale")),
            "action": ("该页已引用这篇。若这篇是新增/刚更新的，核对页内结论是否仍成立："
                       "成立则无需动作；被推翻则 mark_stale 并 update_wiki_page 重写。"),
        })

    # 主题相关但未引用：用检索找同题的 wiki 页
    related = []
    try:
        import retriever as R
        paper = (R.M.get("papers") or {}).get(source_key) or {}
        probe = " ".join(x for x in (paper.get("title", ""), (paper.get("abstract") or "")[:200]) if x).strip()
        if probe and R.STATE.get("ready"):
            for h in R.search(probe, topk, "blend"):
                if not h.get("is_wiki"):
                    continue
                pid = h.get("key", "")
                if pid in cited_ids or pid not in by_id:
                    continue
                p = by_id[pid]
                related.append({
                    "id": pid, "title": p.get("title", ""), "kind": p.get("kind", ""),
                    "relation": "same_topic_not_cited",
                    "stale": bool(p.get("stale")),
                    "action": ("这页讲同一主题却没引用这篇。read_source 读完这篇后，"
                               "若它补充或挑战了页内论点，用 update_wiki_page(mode='append') 并入并加引注。"),
                })
    except Exception as e:
        print(f"[wiki] propose_updates 检索相关页失败：{e}", file=sys.stderr, flush=True)

    affected += related
    hint = []
    if not affected:
        hint.append("没有既有页与这篇直接相关。考虑为它新建 concept / entity 页，"
                    "并用 set_wiki_links 接进已有的图。")
    else:
        hint.append(f"这篇触及 {len(affected)} 个已有页（{len(cited_ids)} 个直接引用它，"
                    f"{len(related)} 个同题未引用）。逐页处理，别只改一页。")
    if len(affected) < 3:
        hint.append("gist 的经验是一篇源常触及 10-15 页。若你的 wiki 还很小，"
                    "这说明该补更多 entity / concept 页，而不是这篇不重要。")
    return {"key": source_key, "n_affected": len(affected), "affected": affected, "hints": hint}


def lint(min_mentions=2):
    """gist 三大操作之一：wiki 健康体检。纯读（index.json + 各页 .md），不碰检索、不调 LLM、零副作用。

    查七类问题：
      orphan           孤儿页：既不链出也无人链入（gist: "orphan pages with no inbound links"）
      stale            已被标脏、等待重生的页
      broken_link      frontmatter links 指向了不存在的页 id
      body_broken_link EN-W5：**正文**里 [[page-id]] / [[page-id|文字]] 指向不存在的页
                       （schema v2 允许正文互链，这是与之配套的核验）
      no_sources       没有任何来源论文的页（无 provenance，最可疑）
      degraded         未配 AI 模型 / 生成失败的降级页（内容只是片段清单）
      missing_concept  被 >=min_mentions 个页在标题里提到、却没有自己独立页的概念

    返回结构化结果 + 建议动作，供 agent 逐条处理，或在 UI 里展示。
    刻意**不做矛盾检测**：那需要 LLM 判断，且规约明确「矛盾只作未核实提示，不落成 wiki 断言」。"""
    idx = load_index()
    pages = idx.get("pages", [])
    by_id = {p["id"]: p for p in pages}

    inbound = {p["id"]: [] for p in pages}
    broken = []
    for p in pages:
        for t in (p.get("links") or []):
            if t in inbound:
                inbound[t].append(p["id"])
            else:
                broken.append({"page_id": p["id"], "title": p.get("title", ""), "dangling": t})

    orphan, stale_pages, no_src, degraded_pages = [], [], [], []
    for p in pages:
        pid, brief = p["id"], {"id": p["id"], "title": p.get("title", ""), "kind": p.get("kind", "")}
        if not (p.get("links") or []) and not inbound[pid]:
            orphan.append(brief)
        if p.get("stale"):
            stale_pages.append(brief)
        if not (p.get("sources") or []):
            no_src.append(brief)
        if is_degraded(p.get("generated_by", "")):
            degraded_pages.append({**brief, "reason": degraded_reason(p.get("generated_by", ""))})

    # 被多页标题提及、却无独立页的概念：用已有 concept 页的 slug 反查
    have = {p.get("subject") or p.get("title", "") for p in pages if p.get("kind") == "concept"}
    titles = [p.get("title", "") for p in pages]
    mentions = {}
    for p in pages:
        subj = (p.get("subject") or "").strip()
        if not subj or len(subj) < 2:
            continue
        n = sum(1 for t in titles if subj in t)
        if n >= min_mentions and subj not in have:
            mentions[subj] = n
    missing_concept = [{"concept": k, "mentioned_in": v}
                       for k, v in sorted(mentions.items(), key=lambda kv: -kv[1])][:10]

    # EN-W5：正文断链——正则解析 frontmatter 之外的正文里的 [[page-id]] / [[page-id|文字]]，
    # 目标不在 index 里即断链。同页同目标只报一次（一页里重复引同一个坏链没必要刷屏）。
    body_broken = []
    for p in pages:
        path = page_path(p["id"], p.get("kind", "answer"))
        if not path.exists():
            continue
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            continue
        body_txt = re.sub(r"^---[\s\S]*?\n---\n?", "", txt)      # 剥掉 frontmatter，只查正文
        seen_t = set()
        for m in re.finditer(r"\[\[([^\[\]|\n]+?)(?:\|[^\[\]\n]*)?\]\]", body_txt):
            target = m.group(1).strip()
            if target and target not in by_id and target not in seen_t:
                seen_t.add(target)
                # 字段名与 broken_link 同构用 dangling（前端体检面板按同一渲染路径读 x.dangling）
                body_broken.append({"page_id": p["id"], "title": p.get("title", ""), "dangling": target})

    issues = {"orphan": orphan, "stale": stale_pages, "broken_link": broken,
              "body_broken_link": body_broken,
              "no_sources": no_src, "degraded": degraded_pages, "missing_concept": missing_concept}
    total = sum(len(v) for v in issues.values())
    return {
        "n_pages": len(pages), "n_issues": total,
        "healthy": total == 0,
        "issues": issues,
        "suggestions": _lint_suggestions(issues),
    }


def _lint_suggestions(issues):
    s = []
    if issues["orphan"]:
        s.append(f"{len(issues['orphan'])} 个孤儿页：用 set_wiki_links 把它们接进知识图，"
                 f"或确认它们确实该独立存在。")
    if issues["stale"]:
        s.append(f"{len(issues['stale'])} 个页已标过时：读新文献后用 update_wiki_page 重写，"
                 f"再 mark_stale(stale=false) 清除标记。")
    if issues["broken_link"]:
        s.append(f"{len(issues['broken_link'])} 条断链指向已删除的页：用 set_wiki_links(mode='remove') 清掉。")
    if issues.get("body_broken_link"):
        s.append(f"{len(issues['body_broken_link'])} 处正文互链 [[…]] 指向不存在的页："
                 f"改成真实存在的页 id，或先把目标页建出来（update_wiki_page）。")
    if issues["no_sources"]:
        s.append(f"{len(issues['no_sources'])} 个页没有来源论文——没有 provenance 的综合不可信，"
                 f"补 sources 或删掉。")
    if issues["degraded"]:
        s.append(f"{len(issues['degraded'])} 个降级页（未配 AI 模型时生成的片段清单）："
                 f"配好模型后重新生成。")
    if issues["missing_concept"]:
        names = "、".join(x["concept"] for x in issues["missing_concept"][:3])
        s.append(f"这些概念被反复提及却没有独立页：{names}…… 考虑各建一个 concept 页。")
    if not s:
        s.append("综合层健康：无孤儿页、无过时页、无断链、无缺 provenance 的页。")
    return s


# ═══ 对外：一键"不保存"——丢弃某页（§6.4 opt-out；仅人用，不给 agent）═══════
def delete_page(page_id):
    """删三处：data/wiki/**/<id>.md 文件 + index.json 条目（含重建 by_source）+ LanceDB wiki 行。
    幂等：缺哪处删哪处。返回 {deleted, md, table}。**只应由 UI/HTTP 触发，绝不暴露为 MCP 工具。**"""
    with _INDEX_LOCK:            # 读 index → 删 md → 改 index 全程串行，避免与并发保存互相覆盖
        idx = load_index()
        meta = next((p for p in idx.get("pages", []) if p.get("id") == page_id), None)

        md_removed = False
        for k in KINDS:                              # 跨目录兜底删 md
            p = page_path(page_id, k)
            if p.exists():
                try:
                    p.unlink(); md_removed = True
                except Exception as e:
                    print(f"[wiki] 删 md 失败 {p}：{e}", file=sys.stderr, flush=True)

        if meta:                                      # 删 index 条目并重建 by_source 反查
            pages = [p for p in idx.get("pages", []) if p.get("id") != page_id]
            idx["pages"] = pages
            idx["by_source"] = _build_by_source(pages)
            _save_index(idx)

    table_removed = False
    try:
        import retriever as R
        table_removed = R.delete_wiki_page(page_id)   # 删表行 + 内存 records/wiki（幂等）
    except Exception as e:
        print(f"[wiki] 删表行失败：{e}", file=sys.stderr, flush=True)

    if md_removed:
        try:                                    # 让删除也进版本历史，否则 git 工作区永远是脏的
            import wiki_vcs as V
            V.record_delete(page_id)
        except Exception as e:
            print(f"[wiki] 记录删除失败（不影响删除本身）：{e}", file=sys.stderr, flush=True)

    return {"deleted": bool(meta) or md_removed or table_removed,
            "md": md_removed, "table": table_removed}
