# -*- coding: utf-8 -*-
"""
LocalKB MCP server —— 让 Claude Code / Codex 通过 MCP 原生调用本地知识库。
零第三方依赖（纯 stdlib + requests；不装 mcp 包，不污染 venv、分发免依赖）。
传输：stdio，newline-delimited JSON-RPC 2.0。
工具：search_localkb（检索）/ localkb_status（状态）/ localkb_build（建库）。
"""
import sys, json, subprocess, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import requests

# stdio 必须 UTF-8（中文工具描述/结果）；日志一律走 stderr，绝不污染 stdout 的 JSON-RPC 通道
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

URL = C.DAEMON_URL
PROTO = "2024-11-05"

def log(*a):
    print("[mcp-localkb]", *a, file=sys.stderr, flush=True)

def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def health():
    try:
        return requests.get(URL + "/health", timeout=3).json()
    except Exception:
        return None

def _server_log():
    """server 子进程 stdout/stderr 落 logs/server.log（换机排障命脉，替代 DEVNULL 静默）。
    取不到日志文件则回退 DEVNULL，绝不让本进程的 stdout（JSON-RPC 通道）被污染。"""
    try:
        C.LOGS.mkdir(parents=True, exist_ok=True)
        f = open(C.LOGS / "server.log", "ab")
        f.write((f"\n===== [{time.strftime('%Y-%m-%d %H:%M:%S')}] MCP 拉起 server.py =====\n")
                .encode("utf-8", "replace"))
        f.flush()
        return f
    except Exception:
        return subprocess.DEVNULL


def ensure_up(wait=120):
    h = health()
    if h and h.get("ready"):
        return True
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
    logf = _server_log()
    subprocess.Popen([sys.executable, str(C.APP / "server.py")],
                     stdout=logf, stderr=logf,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    log("拉起 LocalKB 服务（首次加载模型）...")
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(2)
        h = health()
        if h and h.get("ready"):
            return True
    return False

TOOLS = [
    {
        "name": "search_localkb",
        "description": "检索本地文献知识库（用户自己的 Zotero 库或导入的 PDF 文件夹）。返回带期刊等级、官方页码、可回溯引用的结果，用于查找某主题的相关文献、论点或原文段落。可先用 localkb_status 了解库内篇数与学科。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或主题词，如「深度学习在医学影像中的应用」"},
                "topk": {"type": "integer", "description": "返回条数（默认 8）", "default": 8},
                "sort": {"type": "string", "enum": ["blend", "relevance", "tier"],
                          "description": "排序：blend=相关+权威(默认) / relevance=纯相关 / tier=先期刊层级", "default": "blend"},
                "category": {"type": "string",
                    "description": "限定检索范围到某个知识库分类（可选）。取值来自 list_kb_categories 的 id（kbc_…）、或 topic:<n>、或 zotero:<收藏夹路径>。留空=全库。"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_kb_categories",
        "description": "列出本地知识库的自建「知识库分类」及 AI 主题，返回可用于 search_localkb 的 category id。"
                       "先列分类、再带 category 检索，可把检索聚焦到某一组文献。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_page",
        "description": "把某篇文献的『PDF 顺序页号』解析成『期刊印刷页码』（读者翻期刊看到的那一页）。"
                       "写带页级引注时用它把检索命中的 page 换成正确印刷页；标『页码推算』者为连续性推算、请核对。",
        "inputSchema": {"type": "object", "properties": {
            "key": {"type": "string", "description": "文献 key（取自 search_localkb 结果的 «key:...»）"},
            "pdf_page": {"type": "integer", "description": "检索结果里该 chunk 的 page（PDF 顺序页号）"}},
            "required": ["key", "pdf_page"]},
    },
    {
        "name": "build_digest",
        "description": "半自动研究助手·能力二：给一个子题，返回并写回一节『带期刊印刷页引注的资料汇编综述』"
                       "（含覆盖评级 ◎○△▲▽ 与诚实的资料缺口提示）。写回为 digest 页、标 🤖 未核验、可被检索命中。",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "子题/研究问题"},
            "topk": {"type": "integer", "description": "召回条数（默认 14）", "default": 14}},
            "required": ["query"]},
    },
    {
        "name": "research_outline",
        "description": "半自动研究助手·能力一：给研究主题，返回并写回『选题拆解 + 标题参考 + 三级大纲(★核心/☆辅助)』的框架页。"
                       "论证主线须由学者自定，本工具只做启发。写回为 outline 页、标 🤖 未核验。",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "研究主题/方向"}},
            "required": ["topic"]},
    },
    {
        "name": "suggest_new_sources",
        "description": "半自动研究助手·能力三：给主题，返回『建议新增文献（脚注引文挖掘库内缺失、按被引频次）+ 库内错配（有PDF未深索）+ 覆盖评估』。只读、不写库。",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "研究主题"}},
            "required": ["topic"]},
    },
    {
        "name": "localkb_status",
        "description": "查看本地知识库索引状态（词法/语义/全文各档就绪情况、已索引篇数）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "localkb_build",
        "description": "触发本地知识库建库/更新。stage: light(即时词法,秒级) / semantic(语义,分钟级) / deep(全文深索,数小时)。加了新文献后用来增量更新。",
        "inputSchema": {
            "type": "object",
            "properties": {"stage": {"type": "string", "enum": ["light", "semantic", "deep"],
                                     "description": "建库档位（默认 light）", "default": "light"}},
        },
    },
    {
        "name": "save_synthesis",
        "description": "把一段综合结论回填本地知识库的「综合层」。用 search_localkb 检索后，"
                       "可把你综合出的结论/文献综述存成一张带引用、可累积、之后能被检索到的综合页（answer 页）——"
                       "同类问题下次可直接命中该缓存综合，探索开始累积。每个论断请带 [n] 引用，sources 填所依据论文的 key。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "综合页标题 / 对应的研究问题"},
                "content": {"type": "string", "description": "综合正文（每个论断后用 [n] 标注来源）"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "所依据的论文 key 列表（取自 search_localkb 结果里的 «key:...»）"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "list_wiki",
        "description": "列出本地知识库综合层里已存的 wiki 综合页（answer/concept/topic）。"
                       "动手写综合前先查有没有现成的，避免重复造轮子（先读 index、后写回）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_wiki_page",
        "description": "取某个 wiki 综合页的正文（markdown）+ 其来源的论文级页码引用。"
                       "配合 list_wiki：先列后取，复用已有综合而非从零重写。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string",
                                  "description": "wiki 页 id（来自 list_wiki，如 answer-xxxx / concept-xxxx / topic-N）"}},
            "required": ["id"],
        },
    },
]

def do_tool(name, args):
    if name == "search_localkb":
        if not ensure_up():
            return "错误：知识库服务启动失败（请确认 LocalKB 已安装、Python 环境正常，或查看 logs/server.log）。"
        r = requests.post(URL + "/search", json={"query": args["query"],
                          "topk": args.get("topk", 8), "sort": args.get("sort", "blend"),
                          "category": args.get("category")}, timeout=120).json()
        res = r.get("results", [])
        if not res:
            return f"未检索到与「{args.get('query')}」相关的文献。"
        out = [f"检索「{args.get('query')}」（{r.get('mode')} 模式，{r.get('took_ms')}ms）命中 {len(res)} 条：\n"]
        for i, x in enumerate(res, 1):
            tag = "📝综合" if x.get("is_wiki") else x.get("journal_tier")
            out.append(f"[{i}] ({tag}) {x.get('citation')}  «key:{x.get('key', '')}»")
            out.append(f"    {(x.get('text') or '').strip()[:220]}")
        return "\n".join(out)
    if name == "list_kb_categories":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        cats = requests.get(URL + "/kb/categories", timeout=15).json().get("categories", [])
        tops = requests.get(URL + "/topics", timeout=15).json().get("topics", [])
        out = ["可用知识库分类（把 id 传给 search_localkb 的 category 即可聚焦）："]
        for c in cats:
            out.append(f"- {c['id']}  {c['name']}（{c['count']} 篇，已深索 {c['deep_count']}）")
        for t in tops:
            out.append(f"- topic:{t['id']}  {t['name']}（{t['size']} 篇，AI主题）")
        return "\n".join(out) if (cats or tops) else "暂无分类；可在应用「浏览」里新建。"
    if name == "resolve_page":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        r = requests.get(URL + "/research/pagemap/" + str(args.get("key", "")), timeout=30).json()
        if not r.get("ok"):
            return f"无法解析该篇页码：{r.get('detail', '未知')}"
        pp = str(args.get("pdf_page"))
        e = (r.get("map") or {}).get(pp)
        if not e:
            return f"该篇无 PDF 第 {pp} 页的映射（quality={r.get('quality')}）。"
        approx = e.get("method") in ("interp", "offset", "pdfseq") or e.get("conf", 0) < 0.7
        disp = f"{e['printed']}（页码推算）" if approx else str(e["printed"])
        return f"PDF 第 {pp} 页 → 期刊印刷页码 第 {disp} 页（method={e.get('method')}, 刊期={r.get('issue') or '未解析'}）"
    if name == "build_digest":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        r = requests.post(URL + "/research/digest",
                          json={"query": args.get("query", ""), "topk": args.get("topk", 14), "by_agent": True},
                          timeout=300).json()
        if not r.get("ok"):
            return f"生成资料汇编失败：{r.get('detail', '未知')}"
        cov = r.get("coverage") or {}
        return (f"已生成资料汇编「{r.get('title')}」（id={r.get('id')}，覆盖 {cov.get('symbol', '')}{cov.get('label', '')}，"
                f"{r.get('n_sources')} 篇来源，已写回 wiki 页、可被检索）。用 get_wiki_page({r.get('id')!r}) 取正文。")
    if name == "research_outline":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        r = requests.post(URL + "/research/scope",
                          json={"topic": args.get("topic", ""), "by_agent": True}, timeout=300).json()
        if not r.get("ok"):
            return f"生成大纲失败：{r.get('detail', '未知')}"
        return (f"已生成选题框架「{r.get('title')}」（id={r.get('id')}，{r.get('n_sources')} 篇线索）。"
                f"论证主线请自定；用 get_wiki_page({r.get('id')!r}) 取大纲。")
    if name == "suggest_new_sources":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        r = requests.post(URL + "/research/suggest_sources",
                          json={"topic": args.get("topic", "")}, timeout=120).json()
        if not r.get("ok"):
            return f"建议失败：{r.get('detail', '未知')}"
        cov = r.get("coverage") or {}
        out = [f"主题「{args.get('topic')}」覆盖：{cov.get('symbol', '')}{cov.get('label', '')}"
               f"（命中 {cov.get('n')} 篇，高层级 {cov.get('n_high')} 篇）。", r.get("gap_note", "")]
        mc = r.get("missing_cited") or []
        if mc:
            out.append("\n建议新增（被库内引用但库中缺失，按被引频次）：")
            for it in mc[:12]:
                out.append(f"- {it.get('author', '')}《{it.get('title', '')}》（被引 {it.get('freq')} 次）")
        mm = r.get("mismatch_undeep") or []
        if mm:
            out.append("\n库内已有但未深索（建议深索纳入本主题）：")
            for it in mm[:10]:
                out.append(f"- {it.get('citation') or it.get('title')}")
        return "\n".join(x for x in out if x)
    if name == "localkb_status":
        h = health() or {"status": "down"}
        try:
            h["wiki_pages"] = len(requests.get(URL + "/wiki/list", timeout=10).json().get("pages", []))
        except Exception:
            pass
        h["wiki_schema_md"] = str(C.WIKI_SCHEMA_MD)   # agent 去这里读综合层的写回规约（等价 CLAUDE.md）
        return json.dumps(h, ensure_ascii=False)
    if name == "localkb_build":
        if not ensure_up():
            return "服务启动失败"
        stage = args.get("stage", "light")
        ep = {"light": "/index/light", "semantic": "/index/semantic", "deep": "/index/deep"}[stage]
        body = {"scope": "all"} if stage == "deep" else {}
        r = requests.post(URL + ep, json=body, timeout=(900 if stage == "light" else 10)).json()
        return json.dumps(r, ensure_ascii=False)
    if name == "save_synthesis":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        body = {"query": args.get("title", ""), "answer": args.get("content", ""),
                "sources": [{"key": k} for k in (args.get("sources") or [])],
                "by_agent": True, "model": "agent"}   # 标 🤖 未核验；默认采纳、立即可检索（§6.4）
        r = requests.post(URL + "/wiki/answer", json=body, timeout=120).json()
        if r.get("ok"):
            state = "已入表可检索" if r.get("indexed") else "已存盘，重建索引后可检索"
            return f"已沉淀为综合页：{r.get('title')}（id={r.get('id')}，{state}，引用 {r.get('n_sources')} 篇）。"
        return "沉淀失败：" + str(r.get("detail") or r)
    if name == "list_wiki":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        pages = requests.get(URL + "/wiki/list", timeout=30).json().get("pages", [])
        if not pages:
            return "综合层还没有任何 wiki 页（可用 save_synthesis 写回第一条）。"
        out = [f"综合层已有 {len(pages)} 页（动手前先看有没有现成的）："]
        for p in pages:
            flag = "🤖未核验" if p.get("by_agent") else "🧑"
            stale = "·⚠过时" if p.get("stale") else ""
            out.append(f"- [{p.get('id')}] {p.get('kind')}{stale} {flag} {p.get('title', '')}"
                       f"（基于 {p.get('n_sources', 0)} 篇 · {str(p.get('generated_at', ''))[:10]}）")
        return "\n".join(out)
    if name == "get_wiki_page":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        pid = args.get("id", "")
        r = requests.get(URL + "/wiki/page/" + pid, timeout=30)
        if r.status_code == 404:
            return f"无此综合页：{pid}（先用 list_wiki 查 id）。"
        p = r.json()
        head = (f"# {p.get('title', '')}\n（{p.get('kind')} · 基于 {len(p.get('sources', []))} 篇 · "
                f"生成于 {str(p.get('generated_at', ''))[:10]} · 模型 {p.get('generated_by', '') or '未知'}"
                f"{'·⚠可能已过时' if p.get('stale') else ''}）")
        srcs = "\n".join(f"[{i+1}] {s.get('citation') or s.get('key')}"
                         for i, s in enumerate(p.get("sources", [])))
        return head + "\n\n" + p.get("markdown", "") + ("\n\n来源页级引用：\n" + srcs if srcs else "")
    return f"未知工具：{name}"

def main():
    log("MCP server 就绪（stdio）")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        m, rid = req.get("method"), req.get("id")
        if m == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTO,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "localkb", "version": "1.0.0"}}})
        elif m == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif m == "tools/call":
            try:
                text = do_tool(req["params"]["name"], req["params"].get("arguments", {}))
                send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"content": [{"type": "text", "text": "错误：" + str(e)}], "isError": True}})
        elif m == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif m and m.startswith("notifications/"):
            pass  # 通知类无需回复
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found: " + str(m)}})

if __name__ == "__main__":
    main()
