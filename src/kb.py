# -*- coding: utf-8 -*-
"""PaperPiggy 直读桥 —— 不经 daemon、不经 MCP，直接读 data/ 的只读命令行。

【为什么存在】Cowork / 远程会话跑在与 Windows 宿主隔离的 Linux 侧：
localkb MCP 那套是 stdio 起 Windows 的 pythonw.exe，远程侧起不来；
retriever daemon 绑在宿主 127.0.0.1:8770，远程侧没有到宿主的路由，HTTP 也打不通。
但 D:\\PaperPiggy 整个目录是挂载可读的 —— 所以绕开服务端，直接读索引与数据文件。

【口径】只读。召回走库里现成的 bm25s 词法索引 + 应用自己的 jieba 法律词典与同义词扩展，
与应用的词法通道同源；精排走应用同一个 reranker（API 模式下即 SiliconFlow 的
bge-reranker-v2-m3，与 search_localkb 同款同参），期刊权重加成也用同一条 blend 公式。
**仍然没有向量召回**——dense 那一路要读 LanceDB，远程侧装不动，故语义相近但字面不沾的
文献仍可能漏召回。所以：可以用它发现文献、判断相关性排序，**不能据以断言「库里没有 X」**。
写操作一概不做（wiki／综合页／建库都不碰），唯一例外是 memory append，那是纯文本文件。

用法见 `python3 kb.py --help`。
"""
import sys, os, json, re, argparse, textwrap
from pathlib import Path

def _locate_app():
    """定位应用模块目录。两种落点都要认，且**只有分发包才劫持数据落点**：
       · 分发/正式安装：<root>/0_Agent资料库/工具/kb.py，模块在 <root>/app、数据在 <root>/data
       · 开发仓库：<repo>/src/kb.py，模块就在同目录——此时一个环境变量都不设，
         让 config.py 自己按开发态算（硬设 LOCALKB_DATA 会把开发索引指到不存在的地方）。"""
    here = Path(__file__).resolve()
    if len(here.parents) >= 3:
        packed = here.parents[2] / "app"
        if (packed / "config.py").exists():                 # 分发包：<root>/app 里是模块
            return packed, packed.parent, True
        if (here.parents[2] / "config.py").exists():        # 开发仓库：资料库铺在 src/ 之下
            return here.parents[2], here.parents[2], False
    if (here.parent / "config.py").exists():                # 直接跑 src/kb.py
        return here.parent, here.parent, False
    raise SystemExit("找不到应用模块目录（config.py）。kb.py 要放在 "
                     "<PaperPiggy>/0_Agent资料库/工具/ 下，或开发仓库的 src/ 下。")


APP, ROOT, _PACKED = _locate_app()
if _PACKED:
    os.environ.setdefault("LOCALKB_DATA",   str(ROOT / "data"))
    os.environ.setdefault("LOCALKB_MODELS", str(ROOT / "models"))
sys.path.insert(0, str(APP))

import logging

# ── 零安装引导 ────────────────────────────────────────────────────
# jieba 是纯 python，直接借应用自带的那份（版本与建库时一致，连法律词典口径都同源）；
# numpy 用远程侧现成的；bm25s 借不了（它依赖 scipy，而自带的 scipy 是 Windows 二进制轮子），
# 所以检索评分自己用 numpy 算——见 _BM25Numpy。于是全程不装任何包。
_VENDOR = ROOT / "python" / "Lib" / "site-packages"
try:
    import jieba                                   # noqa: F401  远程侧自己装过就用自己的
except ImportError:
    if _VENDOR.exists():
        sys.path.append(str(_VENDOR))              # 追加而非插入：别让 Windows 二进制包盖住本地的

import config as C
import document_formats as DF
import textutil as T
from textutil import tokenize, load_legal_synonyms
import jieba
jieba.setLogLevel(logging.CRITICAL)      # jieba import 时把自己的 logger 设成 DEBUG，必须在其后压掉
import journal_tiers as JT
import cite_format as CF

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')


# ══ 词表与检索（与 retriever.py 的词法通道同源）══════════════════════
def expand_tokens(query, toks):
    """查询侧同义词 OR 扩展；逐字对齐 retriever._expand_tokens。"""
    if not getattr(C, "SYN_EXPAND", True) or not toks:
        return toks
    try:
        groups = load_legal_synonyms()
    except Exception:
        return toks
    if not groups:
        return toks
    ql = (query or "").lower()
    tset = set(toks)
    extra = []
    for g in groups:
        hit = {w for w in g if w in tset or (len(w) >= 3 and _CJK_RE.search(w) and w in ql)}
        if not hit:
            continue
        for w in g - hit:
            for t in tokenize(w):
                if t not in tset:
                    tset.add(t)
                    extra.append(t)
    return toks + extra


def cap_per_key(chunk_ids, k, per_key):
    """每篇最多取 per_key 块；逐字对齐 retriever._cap_per_key。"""
    if per_key <= 0:
        return chunk_ids[:k]
    seen, out = {}, []
    for cid in chunk_ids:
        kk = str(cid).split("::", 1)[0]
        n = seen.get(kk, 0)
        if n >= per_key:
            continue
        seen[kk] = n + 1
        out.append(cid)
        if len(out) >= k:
            break
    return out


_PAPERS = None

def papers():
    global _PAPERS
    if _PAPERS is None:
        d = {}
        with open(C.PAPERS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    p = DF.normalize_record(json.loads(line))
                    d[p["key"]] = p
        _PAPERS = d
    return _PAPERS


_DEEP = None

def deep_stems():
    """已深索 = data/extracted/<stem>.json 存在（与 read_source 的判据一致）。"""
    global _DEEP
    if _DEEP is None:
        try:
            _DEEP = {f.stem for f in C.EXTRACTED.glob("*.json")}
        except Exception:
            _DEEP = set()
    return _DEEP


def is_deep(p):
    return (p.get("stem") or T.safe_name(p.get("key", ""))) in deep_stems()


_CHUNK_CACHE = {}

def chunks_of(key):
    if key in _CHUNK_CACHE:
        return _CHUNK_CACHE[key]
    p = papers().get(key) or {}
    stem = p.get("stem") or T.safe_name(key)
    f = C.CHUNKS / f"{stem}.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        data = []
    _CHUNK_CACHE[key] = data
    return data


def chunk_by_id(cid):
    key = cid.split("::", 1)[0]
    for c in chunks_of(key):
        if c.get("chunk_id") == cid:
            return c
    return None


def printed_of(key, page):
    try:
        import page_map as PM
        return (PM.printed(key, page) or {}).get("display", "")
    except Exception:
        return ""


class _BM25Numpy:
    """直接读 bm25s 存盘的 CSC 三件套算分，只依赖 numpy。

    bm25s 建库时已把每个 (词, 文档) 的 BM25 分预先算进矩阵，检索期就是「按查询词取列、
    往文档号上累加」——即 bm25s.get_scores_from_ids 的那三行。所以这里不是另写一套打分，
    是把同一份分数用同样的方式加起来，结果与 bm25s 逐位相同（已对拍）。"""

    def __init__(self, d):
        import numpy as np
        self.np = np
        self.data = np.load(d / "data.csc.index.npy", mmap_mode="r")
        self.indices = np.load(d / "indices.csc.index.npy", mmap_mode="r")
        self.indptr = np.load(d / "indptr.csc.index.npy", mmap_mode="r")
        self.vocab = json.loads((d / "vocab.index.json").read_text(encoding="utf-8"))
        self.params = json.loads((d / "params.index.json").read_text(encoding="utf-8"))
        self.num_docs = int(self.params["num_docs"])

    def retrieve(self, tokens, k):
        np = self.np
        cols = [self.vocab[t] for t in tokens if t in self.vocab]
        sc = np.zeros(self.num_docs, dtype=np.float32)
        for j in cols:
            a, b = int(self.indptr[j]), int(self.indptr[j + 1])
            if b > a:
                np.add.at(sc, self.indices[a:b], self.data[a:b])
        k = min(k, self.num_docs)
        top = np.argpartition(-sc, k - 1)[:k]
        top = top[np.argsort(-sc[top])]
        return top, sc[top], len(cols)


_BM25 = None

def bm25():
    global _BM25
    if _BM25 is None:
        try:
            import numpy  # noqa: F401
        except ImportError:
            sys.exit("远程侧缺 numpy。跑一次：pip3 install numpy")
        r = _BM25Numpy(C.DATA / "bm25")
        ids = json.loads((C.DATA / "bm25" / "bm25_ids.json").read_text(encoding="utf-8"))
        _BM25 = (r, ids)
    return _BM25


def snippet(text, toks, width=220):
    """取命中词最密的一段窗口，与应用检索片段长度一致（220 字）。"""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= width:
        return t
    best, best_hits = 0, -1
    step = max(1, width // 4)
    for s in range(0, max(1, len(t) - width + 1), step):
        w = t[s:s + width]
        h = sum(w.count(x) for x in toks if len(x) >= 2)
        if h > best_hits:
            best, best_hits = s, h
    return ("…" if best else "") + t[best:best + width] + ("…" if best + width < len(t) else "")


def tier_of(p):
    return p.get("journal_tier") or JT.tier_of(p.get("journal", "")) or "未知"


# ══ 精排与加权（与 retriever 的 API 通道同源）══════════════════════
def api_conf():
    """API 后端配置；不是 api 模式、或没配 key 就返回 None（此时只有词法通道）。
       本地 ONNX 重排在远程侧跑不动（要 onnxruntime + 1.1G 模型），故不做回退。"""
    try:
        import settings as S
        if not S.is_api():
            return None
        a = S.api_conf() or {}
        return a if (a.get("key") or "").strip() else None
    except Exception:
        return None


def _post_json(url, key, payload, timeout):
    """标准库 HTTP POST。远程侧不装 requests——零安装是这座桥的底线。
       返回 (status, 解析后的 json 或原始错误串)。"""
    import urllib.request, urllib.error
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")[:300]
        except Exception:
            return e.code, ""


def _client_err(code, model=""):
    """4xx 翻人话。优先借应用自己的那份，保证与设置页提示同一口径。
       （踩过的坑：硅基流动余额为 0 时连免费模型都 403，看着像密钥错。）"""
    try:
        import reranker as RR
        return RR._client_err(code, model)
    except Exception:
        if code in (401, 403):
            return "密钥无效或余额不足，请检查 API Key 与余额"
        return "重排 API 客户端错误（HTTP %s）" % code


def api_rerank(query, docs, conf, batch=64, timeout=60):
    """调 /rerank 给每段正文打分（0~1，越高越相关）。退避策略照抄 reranker.APIReranker：
       429/5xx/网络层退避重试，4xx 快速失败（重试无用）。返回与 docs 等长、按原序的分数。"""
    import time
    base = (conf.get("base") or "https://api.siliconflow.cn/v1").rstrip("/")
    key = conf.get("key") or ""
    model = conf.get("rerank_model") or getattr(C, "RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    out = []
    for i in range(0, len(docs), batch):
        chunk = list(docs[i:i + batch])
        last = None
        for attempt in range(5):
            code, body = _post_json(base + "/rerank", key,
                                    {"model": model, "query": query,
                                     "documents": chunk, "return_documents": False}, timeout)
            if code == 429:
                last = "429 限流"; time.sleep(min(30, 2 ** attempt)); continue
            if code in (500, 502, 503, 504):
                last = "%s 服务端繁忙" % code; time.sleep(min(10, 1.5 ** attempt)); continue
            if code and 400 <= code < 500:
                raise RuntimeError(_client_err(code, model))
            if code == 200 and isinstance(body, dict):
                sc = [0.0] * len(chunk)
                for it in body.get("results", []):
                    idx = it.get("index", 0)
                    if 0 <= idx < len(chunk):
                        sc[idx] = float(it.get("relevance_score", 0.0))
                out.extend(sc); break
            last = "HTTP %s：%s" % (code, str(body)[:120]); time.sleep(min(10, 1.5 ** attempt))
        else:
            raise RuntimeError("重排 API 连续失败：%s" % last)
    return out


_WEIGHT_MEMO = {}

def weight_of(p):
    """连续期刊权重 journal_weight∈[0,1]，与 retriever._weight_res 同源（走 grading_svc）。
       取不到就返回 None，由 blend_bonus 回退旧离散档。"""
    k = p.get("key", "")
    if k in _WEIGHT_MEMO:
        return _WEIGHT_MEMO[k]
    try:
        import grading_svc as GS
        wr = GS.evaluate_paper(dict(p), compute=True)
    except Exception:
        wr = None
    _WEIGHT_MEMO[k] = wr
    return wr


def blend_bonus(p, wr, lex):
    """逐字对齐 retriever._blend_bonus：优先连续权重，回退旧离散 TIER_BONUS。
       lex=True 是未精排的词法分（尺度大得多），加成 ×3；精排后是 0~1，×1。"""
    scale = 3 if lex else 1
    if wr and wr.get("weight") is not None:
        return wr["weight"] * getattr(C, "WEIGHT_BONUS_SCALE_API", 0.30) * scale
    return getattr(C, "TIER_BONUS", {}).get(tier_of(p), 0.0) * scale


def _pick(ranked, topk, per_key_limit):
    """按分数序选最终 topk，并复刻 retriever 的 meta/chunk 顶替：
       同一篇既有摘要行又有正文行时只留正文（否则同篇既出摘要又出正文，来源多样性被自己吃掉）。
       ranked: [(cid, score, paper)]，已按分数降序。"""
    picked, per_key, meta_idx = [], {}, {}
    for cid, sc, p in ranked:
        k = cid.split("::", 1)[0]
        is_meta = cid.endswith("::meta")
        if is_meta and any(c.split("::", 1)[0] == k and not c.endswith("::meta")
                           for c, _, _ in picked):
            continue
        if not is_meta and k in meta_idx:
            picked[meta_idx.pop(k)] = (cid, sc, p)      # 正文顶替已入选的摘要，名额不变
            continue
        if per_key.get(k, 0) >= per_key_limit:
            continue
        per_key[k] = per_key.get(k, 0) + 1
        picked.append((cid, sc, p))
        if is_meta:
            meta_idx[k] = len(picked) - 1
        if len(picked) >= topk:
            break
    return picked


def _body_of(cid, p):
    """(正文, 页码, 小标题)。meta 行没有正文，用摘要——与应用喂给 reranker 的 text 同源。"""
    if cid.endswith("::meta"):
        return (p.get("abstract") or p.get("title") or ""), None, ""
    c = chunk_by_id(cid) or {}
    return (c.get("text") or ""), c.get("page"), (c.get("heading") or "")


def do_search(a):
    q = a.query
    toks = expand_tokens(q, tokenize(q))
    if not toks:
        print("查询分词为空。"); return
    r, ids = bm25()
    per_key = a.per_key
    # 召回要喂满精排池：精排只能在召回给它的东西里挑，池子不满等于白搭一次 API 往返。
    want = min(len(ids), max(a.pool * 8, a.topk * 8 * max(1, per_key), 200))
    res, scs, n_hit = r.retrieve(toks, k=want)
    if not n_hit:
        print("分词后没有一个词在索引词表里（%s）。换个说法再试。" % " / ".join(toks)); return
    order = [(ids[int(i)], float(s)) for i, s in zip(res, scs) if s > 0]

    if a.scope == "meta":
        order = [x for x in order if x[0].endswith("::meta")]
    elif a.scope == "chunk":
        order = [x for x in order if not x[0].endswith("::meta")]
    if a.key:
        want_keys = set(a.key.split(","))
        order = [x for x in order if x[0].split("::", 1)[0] in want_keys]

    score = {cid: s for cid, s in order}
    # 候选池：先按篇限流（v1.1.9 的召回修复——不限流会被一篇深索长文吃光名额），
    # 再截到 pool。精排要重排这一池，所以池子给得比 topk 宽得多。
    pool = cap_per_key([c for c, _ in order], max(a.pool, a.topk * 4), per_key)

    cand = []
    for cid in pool:
        p = papers().get(cid.split("::", 1)[0])
        if p:
            cand.append((cid, score[cid], p))
    if not cand:
        print("命中的块都找不到对应题录（索引与题录不同步？跑 kb.py doctor 看看）。"); return

    # ── 精排 ──
    lex, note, warn = True, "词法 BM25", ""
    conf = None if a.no_rerank else api_conf()
    if conf:
        bodies = [_body_of(cid, p)[0] for cid, _, p in cand]
        try:
            rr = api_rerank(q, bodies, conf)
            cand = [(cid, rr[n], p) for n, (cid, _, p) in enumerate(cand)]
            lex = False
            note = "词法召回 %d 段 → API 精排（%s）" % (
                len(bodies), conf.get("rerank_model") or C.RERANK_MODEL)
        except Exception as e:
            warn = "⚠ 精排失败，已回退纯词法名次：%s" % e
    elif not a.no_rerank:
        warn = "⚠ 未启用精排（检索引擎不是 API 模式，或没配 API Key）——名次是纯词法的。"

    cand.sort(key=lambda x: -x[1])
    picked = _pick(cand, a.topk, per_key)

    wrs = {cid: weight_of(p) for cid, _, p in picked}

    def sortkey(x):
        cid, sc, p = x
        if a.sort == "tier":
            return (JT.rank_of(tier_of(p)), -sc)
        if a.sort == "relevance":
            return (-sc,)
        return (-(sc + blend_bonus(p, wrs.get(cid), lex)),)
    picked.sort(key=sortkey)

    out = []
    for cid, sc, p in picked:
        body, page, heading = _body_of(cid, p)
        fmt = p.get("fulltext_format") or ""
        wr = wrs.get(cid) or {}
        out.append({
            "chunk_id": cid, "key": p["key"], "score": round(sc, 4),
            "title": p.get("title", ""), "author": p.get("author", ""),
            "year": p.get("year", ""), "journal": p.get("journal", ""),
            "tier": tier_of(p), "journal_weight": wr.get("weight"),
            "row_type": "meta" if cid.endswith("::meta") else "chunk",
            "page": page,
            "printed_page": printed_of(p["key"], page) if (page and fmt == "pdf") else "",
            "heading": heading,
            "itemtype": p.get("itemtype", ""),
            "text": snippet(body, toks),
        })

    if a.json:
        print(json.dumps({"query": q, "tokens": toks, "reranked": not lex,
                          "warning": warn, "hits": out}, ensure_ascii=False, indent=1)); return
    print("查询：%s\n分词：%s\n命中 %d 条（%s；无向量召回；每篇最多 %d 段）"
          % (q, " / ".join(toks), len(out), note, per_key))
    if warn:
        print(warn)
    print("─" * 60)
    for n, h in enumerate(out, 1):
        loc = ("p.%s" % h["page"] + ("（印刷 %s）" % h["printed_page"] if h["printed_page"] else "")) \
            if h["page"] else "题录"
        w = "｜w%.2f" % h["journal_weight"] if h["journal_weight"] is not None else ""
        print("[%d] %s" % (n, h["title"]))
        print("    %s｜%s｜%s｜%s%s｜%s｜score %s"
              % (h["author"], h["journal"], h["year"], h["tier"], w, loc, h["score"]))
        if h["heading"]:
            print("    « %s »" % h["heading"])
        print("    «key:%s»  %s" % (h["key"], h["chunk_id"]))
        print(textwrap.indent(textwrap.fill(h["text"], 74), "    "))
        print()


# ══ 读原文（对齐 server.read_source）══════════════════════════════
def article_label(value):
    raw = re.sub(r"\s+", "", str(value or ""))
    number = r"[一二三四五六七八九十百千零〇\d]+"
    if raw.isdigit() or re.fullmatch(number, raw):
        return f"第{raw}条"
    m = re.fullmatch(rf"第({number})条(?:之({number}))?", raw)
    return m.group(0) if m else ""


def join_chunk_texts(cs):
    text = ""
    for c in cs:
        part = str(c.get("text") or "").strip()
        if not part:
            continue
        overlap = 0
        for n in range(min(160, len(text), len(part)), 0, -1):
            if text[-n:] == part[:n]:
                overlap = n
                break
        text += ("\n" if text and not overlap else "") + part[overlap:]
    return text


def do_read(a):
    key = a.key
    p = papers().get(key)
    if not p:
        sys.exit(f"库里没有 key={key}。先用 kb.py search / sources 找。")
    title = p.get("title", "")
    if not p.get("has_fulltext"):
        sys.exit(f"{title}：只有题录、没有全文附件。")
    stem = p.get("stem") or T.safe_name(key)

    art = article_label(a.article) if a.article else ""
    if art:
        cs = [c for c in chunks_of(key) if article_label(c.get("heading")) == art]
        if not cs:
            avail = []
            for c in chunks_of(key):
                h = article_label(c.get("heading"))
                if h and h not in avail:
                    avail.append(h)
            sys.exit(f"{title}：未找到{art}。已识别条文范围："
                     + (f"{avail[0]} 至 {avail[-1]}" if avail else "无"))
        cs.sort(key=lambda c: (int(c.get("page") or 0), str(c.get("chunk_id") or "")))
        body = join_chunk_texts(cs)[:max(500, a.max_chars)]
        print(f"# {title}　{art}\n（{p.get('author','')}｜{p.get('journal','')}｜{p.get('year','')}｜«key:{key}»）\n")
        print(body)
        return

    f = C.EXTRACTED / f"{stem}.json"
    if not f.exists():
        sys.exit(f"{title}：尚未深索，没有可读全文（应用「浏览」页勾选深索）。")
    rec = json.loads(f.read_text(encoding="utf-8"))
    pages = rec.get("pages") or []
    if not any((pg.get("text") or "").strip() for pg in pages):
        sys.exit(f"{title}：提取结果无文字（扫描件未 OCR 或附件缺失）。")

    fmt = rec.get("document_format") or p.get("fulltext_format") or "pdf"
    last = max([int(pg.get("page", 0) or 0) for pg in pages] + [int(rec.get("total_pages", 0) or 0)])
    lo = max(1, a.from_page)
    hi = a.to_page or last
    budget = max(500, a.max_chars)
    used, shown, nxt = 0, 0, None
    head = f"# {title}\n（{p.get('author','')}｜{p.get('journal','')}｜{p.get('year','')}｜{fmt}｜共 {last} 页｜«key:{key}»）"
    print(head + "\n")
    for pg in pages:
        n = int(pg.get("page", 0) or 0)
        if n < lo or n > hi:
            continue
        txt = (pg.get("text") or "").strip()
        if not txt:
            continue
        if used + len(txt) > budget and shown:
            nxt = n
            break
        used += len(txt); shown += 1
        loc = DF.locator_label(fmt, n, pg.get("heading"), pg.get("locator_label"))
        pr = printed_of(key, n) if fmt == "pdf" else ""
        print(f"───── {loc}" + (f"　印刷页 {pr}" if pr else "") + " ─────")
        print(txt + "\n")
    if nxt:
        print(f"[截断：已返回 {shown} 页 {used} 字。续读：kb.py read {key} --from {nxt}]")


# ══ 其余读工具 ═══════════════════════════════════════════════════
def do_meta(a):
    p = papers().get(a.key)
    if not p:
        sys.exit(f"库里没有 key={a.key}。")
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=1)); return
    keep = ["key", "title", "author", "authors", "editors", "translators", "year", "journal",
            "volume", "issue", "official_pages", "doi", "issn", "itemtype", "langid",
            "journal_tier", "has_fulltext", "has_pdf", "fulltext_format",
            "keywords", "abstract"]
    for k in keep:
        v = p.get(k)
        if v in (None, "", [], {}):
            continue
        print(f"{k:18} {v}")
    print(f"{'tier(实时)':18} {tier_of(p)}")
    print(f"{'深索':18} {'是' if is_deep(p) else '否'}")
    idx = _wiki_index()
    cited = [w.get("title","") for w in idx.get("pages", [])
             if any(s.get("key") == a.key for s in w.get("sources", []))]
    if cited:
        print(f"{'被综合页引用':18} " + "；".join(cited))


def do_cite(a):
    p = papers().get(a.key)
    if not p:
        sys.exit(f"库里没有 key={a.key}。")
    hit = dict(p)
    hit["key"] = a.key
    if a.page:
        hit["page"] = a.page
    heading = article_label(a.article) if a.article else ""
    print(CF.footnote(hit, heading=heading) if not a.compact else CF.compact(hit, heading=heading))


def do_page(a):
    import page_map as PM
    r = PM.printed(a.key, a.pdf_page) or {}
    if not r:
        sys.exit("没有该篇的页码映射（未深索或非 PDF）。")
    print(json.dumps(r, ensure_ascii=False))


def do_quote(a):
    import textloc
    try:
        r = textloc.locate(a.text, key=a.key or None, fuzzy=not a.exact)
    except ValueError as e:
        sys.exit(f"核对不了：{e}")
    ms = r.get("matches", [])
    if not ms:
        print("原文里没找到这句" + (f"（限 key={a.key}）" if a.key else "（全库）")
              + "。可能是转述、OCR 差异，或该篇未深索——换更短的原话再试。")
        return
    for m in ms:
        print(f"«key:{m.get('key','')}» {m.get('locator','')}"
              + (f"　印刷页 {m['printed_page']}" if m.get("printed_page") else "")
              + f"　{'精确' if m.get('exact') else '模糊'}")
        print("    " + re.sub(r"\s+", " ", m.get("context", ""))[:300])


def do_sources(a):
    rows = list(papers().values())
    if a.q:
        ql = a.q.lower()
        rows = [p for p in rows if ql in (p.get("title", "") + p.get("author", "")
                                          + p.get("journal", "")).lower()]
    if a.deep == "no":
        rows = [p for p in rows if not is_deep(p)]
    elif a.deep == "yes":
        rows = [p for p in rows if is_deep(p)]
    total = len(rows)
    rows = rows[a.offset:a.offset + a.limit]
    print(f"共 {total} 篇，显示 {a.offset + 1}–{a.offset + len(rows)}\n" + "─" * 60)
    for p in rows:
        print(f"«key:{p['key']}» {p.get('title','')}")
        print(f"    {p.get('author','')}｜{p.get('journal','')}｜{p.get('year','')}｜{tier_of(p)}"
              f"｜{'深索' if is_deep(p) else '未深索'}")


def _wiki_index():
    return json.loads((C.WIKI_DIR / "index.json").read_text(encoding="utf-8"))


_FM_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)


def _wiki_body(pid, kind):
    for sub in ("answers", "concepts", "topics", "digests", "outlines", "entities", "overviews"):
        f = C.WIKI_DIR / sub / f"{pid}.md"
        if f.exists():
            return _FM_RE.sub("", f.read_text(encoding="utf-8"))
    for f in C.WIKI_DIR.rglob(f"{pid}.md"):
        return _FM_RE.sub("", f.read_text(encoding="utf-8"))
    return ""


def do_wiki(a):
    idx = _wiki_index()
    pages = idx.get("pages", [])
    if a.action == "list":
        print(f"综合层共 {len(pages)} 页\n" + "─" * 60)
        for p in pages:
            flag = ("⚠过时 " if p.get("stale") else "") + ("✔已核验" if p.get("verified_at") else "🤖未核验")
            print(f"[{p.get('kind','')}] {p.get('title','')}")
            print(f"    id={p.get('id','')}｜{flag}｜主题：{p.get('theme','') or '（自动）'}｜来源 {len(p.get('sources',[]))} 篇")
    elif a.action == "get":
        p = next((x for x in pages if x.get("id") == a.arg), None)
        if not p:
            sys.exit(f"没有 id={a.arg} 的综合页。先跑 kb.py wiki list。")
        print(f"# {p.get('title','')}\n（{p.get('kind','')}｜{'已核验' if p.get('verified_at') else '未核验'}"
              f"｜主题：{p.get('theme','') or '（自动）'}）\n")
        print(_wiki_body(p["id"], p.get("kind", "")))
        print("\n## 来源")
        for s in p.get("sources", []):
            print(f"- «key:{s.get('key','')}» {s.get('citation','')}")
        if p.get("links"):
            print("\n## 互链\n" + "\n".join("- " + x for x in p["links"]))
    else:  # search
        ql = a.arg.lower()
        hits = [p for p in pages if ql in (p.get("title", "") + p.get("subject", "")
                                           + p.get("theme", "")).lower()]
        for p in hits:
            print(f"[{p.get('kind','')}] {p.get('title','')}  id={p.get('id','')}")
        if not hits:
            print("标题/主题里没有匹配；正文全文匹配请用 grep 扫 data/wiki/。")


def do_statutes(a):
    base = C.DATA / "statutes"
    rows = []
    for d in sorted(base.iterdir()):
        f = d / "metadata.json"
        if not f.exists():
            continue
        m = json.loads(f.read_text(encoding="utf-8"))
        rows.append((d.name, m))
    if a.key:
        for name, m in rows:
            if a.key in (name, m.get("key", ""), m.get("title", "")) or a.key in m.get("title", ""):
                body = (base / name / "body.md").read_text(encoding="utf-8")
                if a.article:
                    art = article_label(a.article)
                    lines, keep, got = body.splitlines(), [], False
                    for ln in lines:
                        if re.match(rf"^#*\s*{art}(?![一二三四五六七八九十百千零〇\d])", ln.strip()):
                            got = True
                        elif got and re.match(r"^#*\s*第[一二三四五六七八九十百千零〇\d]+条", ln.strip()):
                            break
                        if got:
                            keep.append(ln)
                    if not keep:
                        sys.exit(f"{m.get('title','')} 里没找到 {art}。")
                    body = "\n".join(keep)
                print(f"# {m.get('title','')}\n（{m.get('issuing_authority','')}｜{m.get('validity_status','')}"
                      f"｜{m.get('statute_version_label','')}｜施行 {m.get('effective_date','') or '未记'}"
                      f"｜取自 {m.get('fetched_at','')[:10]}｜{m.get('source_url','')}）\n")
                print(body)
                return
        sys.exit(f"没有匹配 {a.key} 的法规。")
    print(f"独立法规库共 {len(rows)} 部\n" + "─" * 60)
    for name, m in rows:
        print(f"{m.get('title','')}")
        print(f"    {name}｜{m.get('issuing_authority','')}｜{m.get('validity_status','')}"
              f"｜{m.get('statute_version_label','') or '版本未标'}｜{m.get('first_article','')}–{m.get('last_article','')}"
              f"（{m.get('article_count','?')} 条）")


MEM_VIEW_MAX_CHARS = 15000      # 与 mcp_server.MEMORY_VIEW_MAX_CHARS 同值，保证两条路径读到的分段一致


def paged(head, body, cont_cmd, part=1):
    head = (str(head or "").strip() + "\n\n") if str(head or "").strip() else ""
    budget = max(2000, MEM_VIEW_MAX_CHARS - len(head) - 400)
    segs, cur, used = [], [], 0
    for ln in str(body or "").splitlines(True):
        while len(ln) > budget:
            if cur:
                segs.append("".join(cur)); cur, used = [], 0
            segs.append(ln[:budget]); ln = ln[budget:]
        if used + len(ln) > budget and cur:
            segs.append("".join(cur)); cur, used = [], 0
        cur.append(ln); used += len(ln)
    if cur:
        segs.append("".join(cur))
    segs = segs or [""]
    n = len(segs)
    i = max(1, min(int(part or 1), n))
    out = head
    if n > 1:
        out += f"（全文 {len(body)} 字，共 {n} 段；本段 {i}/{n}）\n\n"
    out += segs[i - 1].rstrip()
    if i < n:
        out += (f"\n\n━━ 本段结束 ━━ 还有 {n - i} 段未读：{cont_cmd} --part {i + 1}")
    return out


MEM = ROOT / "0_Agent资料库" / "记忆"
MEM_FILES = {"core": MEM / "项目记忆.md", "tools": MEM / "工具经验.md", "log": MEM / "变更日志.md"}


def do_memory(a):
    if a.append:
        tgt = a.target or a.scope
        if tgt.startswith("topic:"):
            name = tgt.split(":", 1)[1].strip()
            f = MEM / "主题档案" / f"{name}.md"
            if not f.exists():
                sys.exit(f"主题档案《{name}》不存在。一个研究主题只有一个档案——"
                         f"先跑 kb.py memory topics 看是不是已有同题的另一个名字；"
                         f"确要新建请在应用里用 append_project_memory 建，桥不代建。")
        else:
            f = MEM_FILES.get(tgt)
            if not f:
                sys.exit("target 只能是 core / tools / log / topic:<主题名>")
        text = a.append.strip()
        if tgt == "core":
            if len(text) > 600:
                sys.exit(f"core 单条上限 600 字，这条 {len(text)} 字。压缩后再写，"
                         f"或改写进 tools／topic:／log。")
            cur = len(f.read_text(encoding="utf-8").splitlines())
            after = cur + len(text.splitlines()) + 3
            if after > 200:
                sys.exit(f"项目记忆.md 现 {cur} 行，写完会到 {after} 行，超过 200 行上限（与应用同规）。"
                         f"先按《core 的容量治理》搬家：整段移进 变更日志.md 或 主题档案/，一字不删只换位置；"
                         f"或把这条压到 {200 - cur - 3} 行以内。")
        from datetime import date
        block = f"\n<!-- {date.today().isoformat()} 由 AI 助手追加 -->\n{text}\n"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(block)
        print(f"已追加到 {f.relative_to(ROOT)}（{len(text)} 字）")
        if tgt == "core":
            n = len(f.read_text(encoding="utf-8").splitlines())
            print(f"提醒：项目记忆.md 现 {n} 行，上限 200 行。")
        return

    scope = a.target or a.scope
    if scope == "topics":
        for f in sorted((MEM / "主题档案").glob("*.md")):
            head = f.read_text(encoding="utf-8").splitlines()
            status = next((l for l in head[:12] if "状态" in l), "")
            print(f"{f.stem}　{status.strip()}")
        return
    if scope.startswith("topic:"):
        f = MEM / "主题档案" / f"{scope.split(':',1)[1].strip()}.md"
        if not f.exists():
            sys.exit(f"没有这份主题档案。可用的：\n  " +
                     "\n  ".join(p.stem for p in sorted((MEM / '主题档案').glob('*.md'))))
        print(f.read_text(encoding="utf-8"))
        return
    if scope == "core":
        print(core_view(a.part)); return
    f = MEM_FILES.get(scope)
    if not f:
        sys.exit("scope 只能是 core / tools / log / topics / topic:<主题名>")
    print(paged(f"# {f.stem}", f.read_text(encoding="utf-8"),
                f"python3 kb.py memory {scope}", a.part))


def core_view(part=1):
    body = ""
    for k in ("core", "tools"):
        body += f"\n{'='*22} {MEM_FILES[k].name} {'='*22}\n\n" + MEM_FILES[k].read_text(encoding="utf-8")
    body += f"\n{'='*22} 主题档案清单（正文默认不读）{'='*22}\n\n"
    for f in sorted((MEM / "主题档案").glob("*.md")):
        head = f.read_text(encoding="utf-8").splitlines()
        st = next((l.strip() for l in head[:12] if "状态" in l), "")
        body += f"- {f.stem}　{st}\n"
    body += ("\n取某一份正文：python3 kb.py memory \"topic:<主题名>\"　"
             "（用户说旧结论作废时，一份都不许取）\n")
    return paged("# 项目记忆 · core 视图（＝ read_project_memory(scope=\"core\")）",
                 body, "python3 kb.py memory core", part)


def do_status(a):
    man = json.loads((C.DATA / "index_manifest.json").read_text(encoding="utf-8"))
    ps = papers()
    deep = sum(1 for p in ps.values() if is_deep(p))
    idx = _wiki_index()
    print("PaperPiggy 直读桥 · 只读 · %s · 无向量召回"
          % ("词法召回 + API 精排" if api_conf() else "纯词法（精排不可用）"))
    print(f"数据目录　　 {C.DATA}")
    print(f"数据源　　　 {man.get('source','')}")
    print(f"文献　　　　 {man.get('papers','?')} 篇（有全文 {man.get('with_fulltext','?')}，"
          f"已深索 {deep}）")
    print(f"词法索引　　 {'就绪' if (C.DATA/'bm25'/'params.index.json').exists() else '缺'}"
          f"　建于 {man.get('light_at','?')}")
    print(f"综合层　　　 {len(idx.get('pages',[]))} 页")
    print(f"独立法规库　 {len([d for d in (C.DATA/'statutes').iterdir() if (d/'metadata.json').exists()])} 部")
    print(f"向量库　　　 {'存在（本桥不使用）' if (C.DATA/'lancedb').exists() else '无'}")


def do_doctor(a):
    """零安装体检：报清楚每个依赖从哪来、索引在不在。不装任何东西。"""
    import numpy, jieba as J
    print(f"{'python'.ljust(16)}{sys.version.split()[0]}　{sys.executable}")
    print(f"{'numpy'.ljust(16)}{numpy.__version__}")
    print(f"{'jieba'.ljust(16)}{'应用自带' if str(_VENDOR) in J.__file__ else '远程侧自有'}　{J.__file__}")
    print(f"{'BM25 打分'.ljust(14)}numpy 直算 CSC（不需要 bm25s／scipy）")
    conf = api_conf()
    if conf:
        print(f"{'精排'.ljust(15)}API {conf.get('rerank_model') or C.RERANK_MODEL}"
              f"　{conf.get('base') or ''}（标准库 urllib，不需要 requests）")
    else:
        try:
            import settings as S
            why = "检索引擎不是 API 模式" if not S.is_api() else "API Key 为空"
        except Exception as e:
            why = f"读设置失败：{e}"
        print(f"{'精排'.ljust(15)}不可用（{why}）——只有词法名次")
    for name, ok in (("bm25 索引", (C.DATA / "bm25" / "params.index.json").exists()),
                     ("papers.jsonl", C.PAPERS_JSONL.exists()),
                     ("extracted/", C.EXTRACTED.exists()),
                     ("wiki/index.json", (C.WIKI_DIR / "index.json").exists())):
        print(f"{name.ljust(16)}{'就绪' if ok else '缺失'}")
    print("\n结论：本桥零安装——不需要 pip 装任何包（精排走标准库 HTTP）。")
    if conf:
        print("自检精排链路：", end="", flush=True)
        try:
            sc = api_rerank("测试", ["未成年人刑事责任年龄", "今天天气不错"], conf, timeout=20)
            print("通　", [round(x, 3) for x in sc])
        except Exception as e:
            print(f"不通　{e}")


def do_boot(a):
    """新会话的唯一入口：一条命令把桥接上并读完项目记忆闸门。"""
    mode = "词法召回 + API 精排" if api_conf() else "纯词法（精排不可用，跑 doctor 看原因）"
    print(f"PaperPiggy 直读桥已接上　·　只读　·　{mode}　·　无向量召回")
    print("能用来发现文献与判断相关性排序，但**不得据以断言「库里没有 X」**"
          "——字面不沾、语义相近的文献仍可能漏召回。凡用其结果，交付时须写明本轮走的是直读桥。\n")
    do_status(a)
    print("\n命令：search / read / meta / cite / page / quote / sources / wiki / statutes / memory"
          "　（`python3 kb.py <命令> -h` 看参数）")
    print("\n" + "─" * 72 + "\n")
    print(core_view(a.part))


def main():
    ap = argparse.ArgumentParser(prog="kb.py", description="PaperPiggy 直读桥（只读）",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("boot", help="★新会话第一条命令：接上桥 + 读项目记忆 core")
    p.add_argument("--part", type=int, default=1); p.set_defaults(fn=do_boot)

    p = sub.add_parser("doctor", help="依赖与索引体检（零安装，不装任何包）")
    p.set_defaults(fn=do_doctor)

    p = sub.add_parser("status", help="库内状态"); p.set_defaults(fn=do_status)

    p = sub.add_parser("search", help="检索（词法召回 + API 精排；无向量召回）")
    p.add_argument("query"); p.add_argument("-k", "--topk", type=int, default=8)
    p.add_argument("--sort", choices=["blend", "relevance", "tier"], default="blend")
    p.add_argument("--per-key", type=int, default=2, help="每篇最多返回几段（0=不限）")
    p.add_argument("--scope", choices=["all", "meta", "chunk"], default="all")
    p.add_argument("--key", default="", help="只在这些 key 内检索，逗号分隔")
    p.add_argument("--pool", type=int, default=200,
                   help="送去精排的候选段数（默认 200，实测足以覆盖一次查询命中的全部文献；"
                        "调大更全但 API 往返更慢，每 64 段一次往返）")
    p.add_argument("--no-rerank", action="store_true",
                   help="跳过 API 精排，只看纯词法名次（离线或想省一次往返时用）")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=do_search)

    p = sub.add_parser("read", help="读某篇原文（PDF 带印刷页码；法规可按条读）")
    p.add_argument("key"); p.add_argument("--from", dest="from_page", type=int, default=1)
    p.add_argument("--to", dest="to_page", type=int, default=0)
    p.add_argument("--max-chars", type=int, default=20000)
    p.add_argument("--article", default="", help="第X条（法规按条读）"); p.set_defaults(fn=do_read)

    p = sub.add_parser("meta", help="单篇完整题录")
    p.add_argument("key"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=do_meta)

    p = sub.add_parser("cite", help="出脚注引注")
    p.add_argument("key"); p.add_argument("--page", type=int, default=0, help="PDF 顺序页，自动换印刷页")
    p.add_argument("--article", default=""); p.add_argument("--compact", action="store_true")
    p.set_defaults(fn=do_cite)

    p = sub.add_parser("page", help="PDF 顺序页 → 期刊印刷页")
    p.add_argument("key"); p.add_argument("pdf_page", type=int); p.set_defaults(fn=do_page)

    p = sub.add_parser("quote", help="核对一句引文是否真在原文里、在哪一页")
    p.add_argument("text"); p.add_argument("--key", default=""); p.add_argument("--exact", action="store_true")
    p.set_defaults(fn=do_quote)

    p = sub.add_parser("sources", help="列题录")
    p.add_argument("--q", default=""); p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--deep", choices=["all", "yes", "no"], default="all"); p.set_defaults(fn=do_sources)

    p = sub.add_parser("wiki", help="综合层：list / get <id> / search <词>")
    p.add_argument("action", choices=["list", "get", "search"])
    p.add_argument("arg", nargs="?", default=""); p.set_defaults(fn=do_wiki)

    p = sub.add_parser("statutes", help="独立法规库：不给 key 列全部；给 key 读正文")
    p.add_argument("key", nargs="?", default=""); p.add_argument("--article", default="")
    p.set_defaults(fn=do_statutes)

    p = sub.add_parser("memory", help="项目记忆：core / tools / log / topics / topic:<名>")
    p.add_argument("scope", nargs="?", default="core",
                   help="core / tools / log / topics / topic:<主题名>")
    p.add_argument("--target", default="", help="写入分区（与位置参数等价，写时用它更醒目）")
    p.add_argument("--append", default="", help="追加一条（唯一的写操作）")
    p.add_argument("--part", type=int, default=1, help="分段读（每段 15000 字，与应用同口径）")
    p.set_defaults(fn=do_memory)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
