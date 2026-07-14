# -*- coding: utf-8 -*-
"""
LocalKB MCP server —— 让 Claude Code / Codex 通过 MCP 原生调用本地知识库。
零第三方依赖（纯 stdlib + requests；不装 mcp 包，不污染 venv、分发免依赖）。
传输：stdio，newline-delimited JSON-RPC 2.0。
工具：search_localkb（检索）/ localkb_status（状态）/ localkb_build（建库）。
"""
import sys, json, subprocess, time
from datetime import datetime, timedelta   # EN-M1：whats_new 按天数换算 since 日期
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


# ══ gist 第 3 层「schema」：让 LLM 成为纪律严明的 wiki 维护者，而非通用聊天机器人 ══
#   此前 agent 连上只能从 localkb_status 拿到一个 WIKI.md **路径字符串**，永远读不到内容。
#   MCP 的 initialize 支持 instructions 字段——把规约直接下发，是整个接入里投入产出比最高的一改。
_INSTRUCTIONS_HEAD = """你连接的是用户的**本地文献知识库**（PaperPiggy / LocalKB）。

它不只是搜索引擎。它有一个**综合层（wiki）**：把对文献的理解持久化成带引用、可累积、互链的页面。
你的角色是这个 wiki 的**维护者**。

工作纪律：
1. 动手综合前，先 list_wiki / get_wiki_page 看有没有现成的页，别重复造轮子。
2. 每个论断后带 [n] 引用，n 对应 sources 里的论文 key。不臆造、不给无出处的断言。
3. 下判断前先 read_source 读原文（逐页正文 + 印刷页码）。不要只凭 220 字检索片段就写综述。
4. 你只能写**综合层**（save_synthesis / build_digest / research_outline / mark_stale）。
   绝不改动文献库、索引、Zotero。你能写、不能删——删除只由用户在应用里操作。
5. 你写回的页会标记为「🤖 未核验」，立即进入检索但会被降权。请对得起这个信任。
6. 覆盖规则：你不能覆盖用户人工保存/核验过的页（会被拒绝）。发现旧页被新文献推翻，
   用 mark_stale 标脏并写清理由，而不是抹掉别人的结论。
7. 矛盾与争议只作「未核实」的只读提示，不要落成 wiki 断言。
8. 新增/更新一篇文献后，用 get_backlinks(key=…) 查哪些综合页引用了它，逐一判断是否需要标脏或重生。

下面是这个 wiki 的结构约定（data/wiki/WIKI.md）：
"""


def _wiki_schema_text():
    """读 WIKI.md 正文随 initialize 下发。读不到就只发纪律部分，不阻塞握手。

    先 ensure_scaffold()：WIKI.md 可能还不存在（新装），或仍是旧 schema 版本
    （升级逻辑此前只在第一次写页时才跑，agent 会拿到过期规约）。
    这里只 import wiki_store（不碰 retriever/lancedb），开销很小。"""
    try:
        import wiki_store as W
        W.ensure_scaffold()
    except Exception as e:
        log("ensure_scaffold 失败（继续用现有 WIKI.md）：", e)
    try:
        if C.WIKI_SCHEMA_MD.exists():
            return C.WIKI_SCHEMA_MD.read_text(encoding="utf-8").strip()
    except Exception as e:
        log("读 WIKI.md 失败：", e)
    return ""


def _workspace_text():
    """Agent 专属工作区说明——跨 agent 的共同底座：任何接上来的助手都读同一套本地文件夹，
       换 agent 也能无缝接上。放在 instructions 里下发（比 skill 通用，Codex/别家 agent 也吃得到）。"""
    try:
        import agent_ws as AW
        AW.ensure_scaffold()
        p = AW.paths_info()
    except Exception as e:
        log("workspace paths 失败：", e)
        return ""
    return (
        "\n\n══ 你的专属工作区（都在用户本机、人类可读；换任何 AI 助手都读这套，务必先看）══\n"
        f"· 项目记忆：{p.get('memory_file','')}\n"
        "  —— 开工前先读它（用户是谁/偏好/已定决策/当前在做）。你**可以直接更新这份文件**保持它是「当前真相」；\n"
        "     历史流水账写到同目录「变更日志.md」，别把项目记忆写成流水账。\n"
        f"· 技能：{p.get('skills_dir','')}（工作流；Claude Code 会自动装到 .claude/skills）\n"
        f"· 参考格式：{p.get('formats_dir','')}（用户放的排版范本；改 docx 格式时保护 Zotero 引注域、不重建文档）\n"
        f"· 交付模板：{p.get('templates_dir','')}\n"
        f"· 定时任务定义：{p.get('tasks_dir','')}（每任务一个「任务.md」：搜什么/多久/成果放哪）\n"
        f"· 交付物落点：{p.get('output_dir','')}\n"
        "  —— 你替用户写的成品放这里，**每个主题一个子文件夹**，附一个 README（用途/引注规范/与其他材料的关系）。\n\n"
        "主动维护（优先级高，别等用户开口）：\n"
        "· 深索一批文献后 / 想维护 wiki 时，先调 pending_wiki_updates 拿受影响页清单，再逐页判断标脏或重写。\n"
        "· 跑完定时任务后，依检索到的时效内容更新相关综合页——时效资料是 wiki 的活水。\n"
        "· 产出交付物前，和用户确认交付形态（篇幅/引注/要不要 .docx），可参照交付模板。\n"
    )


def instructions():
    s = _wiki_schema_text()
    body = _INSTRUCTIONS_HEAD + (s or "（WIKI.md 尚未生成；首次写回综合页时会自动创建。）")
    return body + _workspace_text()


def _maybe_install_skill(client_name=""):
    """Claude Code 接入时把技能包自动装进项目的 .claude/skills/（仅当尚未安装时，**绝不覆盖**用户改过的）。
       其它 agent（Codex/Cursor…）不装——skill 是 Claude 专属格式，它们靠 initialize 下发的通用指令。
       判据：clientInfo.name 含 claude，或当前工作目录已有 .claude/（强信号=Claude Code 项目）。
       全程 try/except，绝不因此中断 MCP 握手。"""
    try:
        import shutil
        cwd = Path.cwd()
        is_claude = ("claude" in str(client_name).lower()) or (cwd / ".claude").exists()
        if not is_claude:
            return
        src = C.APP / "skills" / "localkb-paper"
        if not src.exists():
            return
        dest = cwd / ".claude" / "skills" / "localkb-paper"
        if dest.exists():
            return                          # 已装（可能被用户改过）——尊重之，不覆盖
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        log(f"已自动安装技能包 localkb-paper → {dest}")
    except Exception as e:
        log("技能自动安装跳过：", e)

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
    # BLOCKER 修复：判活按“服务是否应答”而非“索引是否 ready”。
    # 旧逻辑用 ready 判活：全新库（未建索引，ready 恒 False）或重载窗口里，即便 server 正常在跑，
    # 也会再 Popen 一个注定 bind 失败的重复进程、并空等 120s 后误报“服务启动失败”——27 个走 ensure_up
    # 的工具全体死锁且报错误导。改为：只要 /health 有应答就放行，让各端点自己的 503/人话错误透传给 agent。
    if health() is not None:
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
        if health() is not None:   # 只等服务起来能应答，不等 ready（空库 ready 恒 False，会白等满 120s）
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
        "name": "export_disclosure",
        "description": "半自动研究助手·G4：按所选综合页(digest/outline 等的 id)生成《生成式 AI 使用声明》文本（规则拼装、零 LLM），"
                       "用于论文投稿的 AIGC 合规披露。传入相关 wiki 页 id 列表即可。",
        "inputSchema": {"type": "object", "properties": {
            "page_ids": {"type": "array", "items": {"type": "string"},
                         "description": "要纳入声明的综合页 id（如 digest-xxxx / outline-xxxx），可传多个"}},
            "required": ["page_ids"]},
    },
    {
        "name": "localkb_status",
        "description": "查看本地知识库索引状态（词法/语义/全文各档就绪情况、已索引篇数）。查【深索】进度请用 deep_status。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "deep_status",
        "description": "查看本地库【深索】进度：已深索篇数 / 有PDF总数 / 队列剩余 / 是否暂停 / 预计剩余时间（ETA）"
                       "/ 当前在深索或队首的篇。深索前后可随时查，了解深到哪了。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "deep_index",
        "description": "深索用户的本地文献库——切块→你自己写检索摘要→带摘要嵌入，一趟完成，不用「先深索再单独补摘要」。"
                       "用法（循环）：第一次【不带 summaries】调用我 → 我返回 to_summarize（若干篇的 key、标题、正文节选 excerpt）；"
                       "你为每篇写一段【约150字的中文检索摘要】（概括核心主题/研究方法/主要结论，供语义检索用）；"
                       "再【带 summaries=[{key, summary}]】调用我 → 我把上一批带着你的摘要嵌入入库、并返回下一批待写摘要；"
                       "如此循环，直到我返回 finished=true 表示全部深索完成。每批默认 15 篇（可用 batch 调整）。"
                       "若返回 busy=true 说明有其它构建在跑，稍后再调。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summaries": {"type": "array",
                    "description": "上一批你写好的摘要；每项 {key, summary}。首次调用留空。",
                    "items": {"type": "object", "properties": {
                        "key": {"type": "string", "description": "文献 key（取自我上一轮返回的 to_summarize）"},
                        "summary": {"type": "string", "description": "你写的约150字中文检索摘要"}},
                        "required": ["key", "summary"]}},
                "batch": {"type": "integer", "description": "每批处理篇数（默认 15）", "default": 15},
            },
        },
    },
    {
        "name": "localkb_build",
        "description": "触发本地知识库建库/更新。stage: light(即时词法,秒级) / semantic(语义,分钟级) / deep(全文深索)。"
                       "加了新文献后用来增量更新。注意：deep 深索大库很慢，且服务端摘要需 API Key——"
                       "推荐改用 deep_index 让你（Agent）自己写检索摘要，一趟把深索+摘要都做完（无需 API Key、质量可控）。",
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
                       "动手写综合前先查有没有现成的，避免重复造轮子（先读 index、后写回）。"
                       "页数多时用 offset 翻页（返回里会注明总页数与当前 offset）。",
        # EN-M2：分页——大综合层此前一次全量返回，既费 token 又可能被客户端截断
        "inputSchema": {"type": "object", "properties": {
            "offset": {"type": "integer", "default": 0, "description": "跳过前多少条（翻页用）"},
            "limit": {"type": "integer", "default": 100, "description": "本页最多返回多少条"},
        }},
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
    # ── ingest 地基：读原文。检索只给 220 字片段，真正读懂一篇文献要靠这个 ──
    {
        "name": "read_source",
        "description": "读某篇论文的**原文正文**（逐页，附期刊印刷页码）。检索结果只给 220 字片段；"
                       "要真正读懂一篇文献、写综述、或核对引注，必须用这个先读原文。"
                       "key 来自 search_localkb 结果里的 «key:…» 或 list_sources。"
                       "未深索 / 只有题录 / 扫描件时会明确告知原因与补救办法，不会静默返回空。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "论文 key"},
                "from_page": {"type": "integer", "description": "起始 PDF 顺序页（默认 1）", "default": 1},
                "to_page": {"type": "integer", "description": "结束 PDF 顺序页（0 = 直到末页）", "default": 0},
                "max_chars": {"type": "integer",
                              "description": "最多返回多少字（默认 20000）。超出会截断并告诉你 next_page，从那页续读。",
                              "default": 20000},
            },
            "required": ["key"],
        },
    },
    {
        "name": "list_sources",
        "description": "列出知识库里的文献题录。可用 deep='no' 筛出**尚未深索**的篇目——"
                       "那些是还没被读过、值得 ingest 的源。用于驱动「逐篇读入并维护 wiki」的循环。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deep": {"type": "string", "enum": ["yes", "no", "all"], "default": "all",
                         "description": "yes=只列已深索（可 read_source）；no=只列未深索；all=全部"},
                "category": {"type": "string", "description": "限定到某分类 id（来自 list_kb_categories）"},
                "limit": {"type": "integer", "default": 50},
                # EN-M2：分页——千篇大库此前只能看到前 limit 篇，其余永远列不到
                "offset": {"type": "integer", "default": 0, "description": "跳过前多少条（翻页用）"},
            },
        },
    },
    # ── lint 地基：stale 写侧 + by_source 反查 ──
    {
        "name": "mark_stale",
        "description": "把某综合页标记为「已过时」（或清除标记）。当新文献推翻了旧综合、或页内断言不再成立时用。"
                       "标记后该页在检索里显著降权、界面显示 ⚠ 徽标。"
                       "这是健康检查(lint)的核心动作：**不要**直接覆盖别人的结论页，而应标脏并写清理由。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "综合页 id（来自 list_wiki）"},
                "stale": {"type": "boolean", "default": True, "description": "true=标为过时；false=清除标记"},
                "reason": {"type": "string", "description": "为什么过时。务必写清楚——用户会读这句话。"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "get_backlinks",
        "description": "反查关联。给 key（论文）→ 哪些综合页引用了这篇（新增或更新这篇后，据此判断哪些页要标脏/重生）；"
                       "给 page_id（综合页）→ 它引用了哪些论文、与哪些页互链、是不是孤儿页。"
                       "这是 ingest 后「一篇源触及多个 wiki 页」和 lint 的起点。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "论文 key（与 page_id 二选一）"},
                "page_id": {"type": "string", "description": "综合页 id（与 key 二选一）"},
            },
        },
    },
    # ── 维护权：建/改页 + 建互链 ──
    {
        "name": "update_wiki_page",
        "description": "建立或修改一个 wiki 综合页。这是维护 wiki 的主要动作。\n"
                       "kind 可选：answer(问答沉淀) / concept(概念) / topic(主题) / digest(资料汇编) / "
                       "outline(选题框架) / **entity(实体页：作者、机构、案件、制度)** / **overview(总论页：随全库演进的核心论点)**。\n"
                       "mode='append' 把新内容并入既有正文（读完一篇新文献后补充某页时用），'replace' 整体重写。\n"
                       "护栏：不能覆盖用户人工核验过的页（会被拒绝）。每个论断带 [n] 引用，sources 填论文 key。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string",
                            "description": "页 id。新建时自取，建议带类型前缀，如 entity-chenruihua / concept-xxx / overview-main"},
                "kind": {"type": "string",
                         "enum": ["answer", "concept", "topic", "digest", "outline", "entity", "overview"],
                         "description": "页种。新建时必填"},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "markdown 正文，每个论断后带 [n] 引用"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "本页所依据论文的 key 列表（provenance 命脉，别留空）"},
                "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
                "links": {"type": "array", "items": {"type": "string"},
                          "description": "交叉链接到的其它 wiki 页 id（可选，也可事后用 set_wiki_links）"},
            },
            "required": ["page_id", "content"],
        },
    },
    {
        "name": "set_wiki_links",
        "description": "维护某页的交叉链接（wiki 页之间的边）。**这是把一堆孤立页面变成一张知识图的唯一途径**——"
                       "没有 links，每一页都是孤儿，lint 会一直报警。"
                       "只接受已存在的页 id，自动拒绝自链与断链。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "links": {"type": "array", "items": {"type": "string"}, "description": "目标页 id 列表"},
                "mode": {"type": "string", "enum": ["replace", "add", "remove"], "default": "replace"},
            },
            "required": ["page_id", "links"],
        },
    },
    # ── gist 的 Lint 与 Ingest 编排 ──
    {
        "name": "lint_wiki",
        "description": "综合层健康体检（gist 三大操作之一）。查：孤儿页、已过时页、断链、无来源论文的页、"
                       "未配 AI 模型时生成的降级页、被反复提及却没有独立页的概念。返回问题清单 + 建议动作。"
                       "定期跑一次，wiki 才不会烂掉。纯读，不改任何东西。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_mentions": {"type": "integer", "default": 2,
                                 "description": "一个概念被至少多少个页提及才算「该有独立页」"},
            },
        },
    },
    {
        "name": "propose_wiki_updates",
        "description": "**读完一篇文献后必调**。给论文 key，返回这篇影响了哪些既有 wiki 页、每页该怎么改。\n"
                       "两条线索：① 直接引用它的页（结论可能被推翻）；② 讲同一主题却没引用它的页（该更新却没人知道）。\n"
                       "gist 的经验：一篇源常常触及 10-15 个页。拿到清单后逐页执行 "
                       "update_wiki_page / mark_stale / set_wiki_links，别只改一页就收工。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "论文 key"},
                "topk": {"type": "integer", "default": 12, "description": "检索同题页时取多少候选"},
            },
            "required": ["key"],
        },
    },
    # ── EN-M1：论文写作工作流工具（引注排版 / 单篇元数据 / 找相似 / 新进速报 / 引文定位 / 论断核验 / 收单篇）──
    {
        "name": "format_citation",
        "description": "把一篇文献排成规范引注（脚注格式）。写论文脚注时用：key 来自 search_localkb / list_sources，"
                       "pdf_page 传检索命中的 PDF 顺序页号，我会换算成期刊印刷页码再排。"
                       "返回里若有 missing_fields（题录缺字段）或 page_estimated（页码为推算）请提醒用户人工核对。"
                       "注意：引领词（参见/见/转引自）由作者按引用性质自定，本工具不加。"
                       "排注前建议先用 locate_quote 核对引文确实在那一页。",
        "inputSchema": {"type": "object", "properties": {
            "key": {"type": "string", "description": "文献 key"},
            "pdf_page": {"type": "integer", "description": "PDF 顺序页号（可选；给了才带页码引注）"},
            "style": {"type": "string", "enum": ["footnote", "compact"], "default": "footnote",
                      "description": "footnote=法学脚注全格式（默认）/ compact=紧凑格式"}},
            "required": ["key"]},
    },
    {
        "name": "get_source_meta",
        "description": "取**单篇**文献的完整题录与状态：作者/年份/期刊/权重档、有无 PDF、是否深索、摘要、"
                       "法条时效（statute_status）、以及哪些 wiki 综合页引用了它（cited_by_wiki）。"
                       "替代『list_sources 翻找 + get_backlinks 反查』两跳——精读一篇前先调它一次拿全貌。",
        "inputSchema": {"type": "object", "properties": {
            "key": {"type": "string", "description": "文献 key（来自 search_localkb / list_sources）"}},
            "required": ["key"]},
    },
    {
        "name": "similar_sources",
        "description": "给一篇 key，返回**向量近邻**的相似文献（cosine，非关键词匹配）。"
                       "精读完一篇后用它扩展检索面——换角度找到 search_localkb 用词召不回的同题文献。"
                       "需要语义索引（full 模式）且该篇已入向量表；不满足时会明确告知回退办法。",
        "inputSchema": {"type": "object", "properties": {
            "key": {"type": "string", "description": "种子文献 key"},
            "topk": {"type": "integer", "default": 8, "description": "返回条数（默认 8）"}},
            "required": ["key"]},
    },
    {
        "name": "whats_new",
        "description": "列出最近 N 天新入库的文献（按入库时间倒序）。回访一个久未碰的库时先调它，"
                       "了解「上次之后进了什么新东西」。返回的 affected_pages 恒为空数组——"
                       "逐篇分析太贵，请对关心的新篇配合 propose_wiki_updates / get_wiki_page 深入。",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7, "description": "回看多少天（默认 7）"},
            "limit": {"type": "integer", "default": 20, "description": "最多返回多少篇（默认 20）"}},
        },
    },
    {
        "name": "locate_quote",
        "description": "**引注核对地基**：给一句引文，核对它是否真的在原文里、在第几页（PDF 页号 + 期刊印刷页码）。"
                       "写脚注前、以及核查既有文稿的引注时逐条过一遍。默认模糊匹配（容忍 OCR/标点差异），"
                       "exact=false 的命中请人工比对 context。给 key 则只在该篇内找，不给则全库找。",
        "inputSchema": {"type": "object", "properties": {
            "quote": {"type": "string", "description": "要核对的引文原句（建议 15 字以上，太短会到处命中）"},
            "key": {"type": "string", "description": "限定在哪篇里找（可选；不给则全库）"},
            "fuzzy": {"type": "boolean", "default": True, "description": "true=模糊匹配（默认）/ false=严格逐字"}},
            "required": ["quote"]},
    },
    {
        "name": "verify_claim",
        "description": "核验一个**实质论断**是否有库内文献支撑。返回三态："
                       "supported=有证据支持 / mismatch=库内证据与论断相左（可能记错或过度概括）/"
                       "not_in_lib=库里找不到依据。注意 not_in_lib **不等于论断为假**——只说明本库无证据，"
                       "该论断要么删、要么明确标注「作者观点/库外知识」。写完每一节后逐条过实质论断。",
        "inputSchema": {"type": "object", "properties": {
            "claim": {"type": "string", "description": "要核验的论断（一句完整的陈述）"},
            "keys": {"type": "array", "items": {"type": "string"},
                     "description": "限定在哪些篇里核验（可选；不给则全库检索）"},
            "topk": {"type": "integer", "default": 8, "description": "取证条数（默认 8）"}},
            "required": ["claim"]},
    },
    {
        "name": "add_source",
        "description": "把本机一个 PDF 文件收进知识库（只加不删）。用户在对话里给了本地 PDF 路径、想让它进库时用。"
                       "题录由 AI 自动抽取、**待人工核对**（应用里会标「待确认」）。收录后建库在后台跑，"
                       "稍后可用 localkb_status / deep_status 查进度。仅 folder（文件夹）模式可用："
                       "Zotero 模式会拒绝并提示把 PDF 附到 Zotero 条目上。",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "PDF 的本机绝对路径"},
            "note": {"type": "string", "description": "备注（可选，随题录保存）"}},
            "required": ["path"]},
    },
    {
        "name": "pending_wiki_updates",
        "description": "拉取服务器已算好的「待处理综合页更新」清单——最近深索/新增的文献可能影响哪些既有 wiki 页。"
                       "深索一批文献后、或想主动维护 wiki 时**先调它**，直接拿到受影响页清单（无需自己对每篇跑 "
                       "propose_wiki_updates），再逐页 get_wiki_page 判断是否 mark_stale / update_wiki_page。无待办则返回空。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# ══ MCP resources：把 schema / 索引 / 页面暴露成资源，agent 可直接读 ══
RESOURCES = [
    {"uri": "localkb://schema", "name": "WIKI.md — 综合层结构约定",
     "description": "gist 第 3 层 schema：页种、每页结构、写回纪律、检索表现。写回前请读。",
     "mimeType": "text/markdown"},
    {"uri": "localkb://index", "name": "综合层索引",
     "description": "所有 wiki 页的清单（id/kind/title/来源数/是否过时/是否 agent 写回）。",
     "mimeType": "application/json"},
    {"uri": "localkb://lint", "name": "综合层体检报告",
     "description": "当前的孤儿页 / 过时页 / 断链 / 无来源页 / 缺失概念页。",
     "mimeType": "application/json"},
]

# EN-M4：resource template 声明。read_resource 早就实现了 localkb://page/{id}，
# 但从未在任何 list 里声明——agent 根本发现不了这条路。按 MCP 规范走
# resources/templates/list（老客户端不调这个方法，无破坏性）。
RESOURCE_TEMPLATES = [
    {"uriTemplate": "localkb://page/{id}", "name": "某个 wiki 综合页正文",
     "description": "按页 id 直读综合页 markdown（id 来自 list_wiki 或 localkb://index）。",
     "mimeType": "text/markdown"},
]

# ══ MCP prompts：把 gist 的三大操作做成斜杠命令 ══
PROMPTS = [
    {
        "name": "ingest-source",
        "description": "把一篇文献读进 wiki：读原文 → 看它影响哪些页 → 逐页更新 → 建互链（gist 的 Ingest）",
        "arguments": [{"name": "key", "description": "论文 key（可先用 list_sources 找）", "required": True}],
    },
    {
        "name": "lint-wiki",
        "description": "给综合层做体检并修复：孤儿页补互链、过时页重写、断链清理（gist 的 Lint）",
        "arguments": [],
    },
    {
        "name": "query-and-file",
        "description": "回答一个问题，并把好答案沉淀回 wiki，接进已有的知识图（gist 的 Query）",
        "arguments": [{"name": "question", "description": "研究问题", "required": True}],
    },
]


def prompt_text(name, args):
    a = args or {}
    if name == "ingest-source":
        k = a.get("key", "")
        return (f"请把论文 {k} 读进综合层，严格按下面的顺序：\n"
                f"1. read_source(key='{k}') 读原文（长文分页读完，别只读第一页）。\n"
                f"2. propose_wiki_updates(key='{k}') 看它影响了哪些既有页。\n"
                f"3. 对每个受影响页：结论仍成立就跳过；被这篇补充或挑战了，就 "
                f"update_wiki_page(mode='append') 并入并加 [n] 引注；被推翻了就 mark_stale 并写清理由。\n"
                f"4. 若这篇引出了新的实体（作者/机构/案件/制度）或新概念，用 update_wiki_page 建 entity/concept 页。\n"
                f"5. 用 set_wiki_links 把新页接进已有的图——别留孤儿页。\n"
                f"6. 更新 overview 总论页：这篇是强化了还是挑战了现有论点？\n"
                f"7. 最后把你做了什么、触及哪几页，简要报告给我。\n"
                f"gist 的经验是一篇源常触及 10-15 个页。只改一页通常说明你漏了。")
    if name == "lint-wiki":
        return ("请给综合层做一次体检并修复：\n"
                "1. lint_wiki() 拿到问题清单。\n"
                "2. 孤儿页：读它的内容，用 set_wiki_links 接到语义相关的页上。\n"
                "3. 断链：set_wiki_links(mode='remove') 清掉指向已删除页的链接。\n"
                "4. 过时页：read_source 读最新的相关文献，update_wiki_page 重写，再 mark_stale(stale=false)。\n"
                "5. 无来源页：补 sources，或告诉我它为什么该留着。\n"
                "6. 缺失概念页：用 update_wiki_page(kind='concept') 补上，并接进图。\n"
                "改完再跑一次 lint_wiki 确认。全程不要删除任何页——你没有删除权限，"
                "该删的页列给我，由我在应用里删。")
    if name == "query-and-file":
        q = a.get("question", "")
        return (f"请回答：{q}\n\n"
                f"步骤：\n"
                f"1. list_wiki() 看有没有现成的综合页已经回答过——有就直接读它（get_wiki_page），别重复造轮子。\n"
                f"2. search_localkb 检索证据；对关键的几篇 read_source 读原文，不要只凭片段下结论。\n"
                f"3. 写出带 [n] 引注的答案给我看。\n"
                f"4. 如果这个答案有长期价值，用 update_wiki_page 沉淀成一页（kind 自选），"
                f"并用 set_wiki_links 接进已有的知识图。\n"
                f"gist 的原则：好答案应该像导入的文献一样在知识库里复利，而不是消失在聊天记录里。")
    return f"未知 prompt：{name}"

def _err_of(resp):
    """把非 2xx 响应翻成人话。此前 search_localkb 直接 .json() 后读 results，
       服务端 503「索引未就绪」会被吞成「未检索到相关文献」——把真实原因藏了。"""
    try:
        j = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    return str(j.get("detail") or j.get("error") or j.get("msg") or f"HTTP {resp.status_code}")


def do_tool(name, args):
    if name == "search_localkb":
        if not ensure_up():
            return "错误：知识库服务启动失败（请确认 LocalKB 已安装、Python 环境正常，或查看 logs/server.log）。"
        resp = requests.post(URL + "/search", json={"query": args["query"],
                             "topk": args.get("topk", 8), "sort": args.get("sort", "blend"),
                             "category": args.get("category")}, timeout=120)
        if resp.status_code != 200:
            return f"检索失败：{_err_of(resp)}"
        r = resp.json()
        res = r.get("results", [])
        if not res:
            return f"未检索到与「{args.get('query')}」相关的文献。"
        out = [f"检索「{args.get('query')}」（{r.get('mode')} 模式，{r.get('took_ms')}ms）命中 {len(res)} 条：\n"]
        for i, x in enumerate(res, 1):
            tag = "📝综合" if x.get("is_wiki") else x.get("journal_tier")
            st = x.get("statute_status") or ""           # 契约11：法条时效徽标（已修订/已废止）
            out.append(f"[{i}] ({tag}{'·' + st if st else ''}) {x.get('citation')}  «key:{x.get('key', '')}»")
            out.append(f"    {(x.get('text') or '').strip()[:220]}")
        # EN-M3：structuredContent——文本仍是主载体，结构化件供支持 MCP 2025-06 的客户端程序化取用
        return "\n".join(out), {"query": args.get("query"), "mode": r.get("mode"),
                                "took_ms": r.get("took_ms"), "results": res}
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
        # 降级页（未配/失效 key、LLM 调用失败、或库内无命中）按设计不入检索表——别再无条件宣称「可被检索」。
        if r.get("degraded"):
            reason = r.get("degraded_reason") or "未配置 AI 模型 / 调用失败 / 库内无命中"
            return (f"资料汇编「{r.get('title')}」已生成，但为**降级产物**（{reason}）：仅是带源证据清单，"
                    f"**未入检索表、不可被检索**（覆盖 {cov.get('symbol', '')}{cov.get('label', '')}，{r.get('n_sources')} 篇来源）。"
                    f"配好 AI 模型/补足余额后重新生成即得正式综述。用 get_wiki_page({r.get('id')!r}) 取正文。")
        return (f"已生成资料汇编「{r.get('title')}」（id={r.get('id')}，覆盖 {cov.get('symbol', '')}{cov.get('label', '')}，"
                f"{r.get('n_sources')} 篇来源，已写回 wiki 页、可被检索）。用 get_wiki_page({r.get('id')!r}) 取正文。")
    if name == "research_outline":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        r = requests.post(URL + "/research/scope",
                          json={"topic": args.get("topic", ""), "by_agent": True}, timeout=300).json()
        if not r.get("ok"):
            return f"生成大纲失败：{r.get('detail', '未知')}"
        if r.get("degraded"):
            reason = r.get("degraded_reason") or "未配置 AI 模型 / 调用失败 / 库内无命中"
            return (f"已生成选题框架「{r.get('title')}」，但为**降级产物**（{reason}）：为库内线索清单、未入检索表。"
                    f"用 get_wiki_page({r.get('id')!r}) 取大纲。")
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
    if name == "export_disclosure":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        pids = args.get("page_ids") or []
        if not pids:
            return "请提供要纳入声明的综合页 id（page_ids）。"
        r = requests.post(URL + "/research/disclosure", json={"page_ids": pids}, timeout=60).json()
        if not r.get("text"):
            return f"生成 AI 使用声明失败：{r.get('detail', '未知')}"
        return r["text"]
    if name == "localkb_status":
        h = health() or {"status": "down"}
        try:
            # EN-M2 连带：/wiki/list 分页后本页条数≠总数，优先读 total（老后端无 total 再退 len）
            j = requests.get(URL + "/wiki/list", timeout=10).json()
            h["wiki_pages"] = j.get("total", len(j.get("pages", [])))
        except Exception:
            pass
        h["wiki_schema_md"] = str(C.WIKI_SCHEMA_MD)   # agent 去这里读综合层的写回规约（等价 CLAUDE.md）
        return json.dumps(h, ensure_ascii=False)
    if name == "deep_status":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        s = requests.get(URL + "/index/queue", timeout=15).json()
        eta = s.get("eta_seconds")
        eta_s = f"约剩 {max(1, int(eta // 60))} 分钟" if eta else "未知"
        out = [f"深索进度：已深索 {s.get('deep_done')}/{s.get('with_pdf')} 篇（有PDF）。",
               f"队列：待处理 {s.get('pending')}、在跑 {s.get('in_flight')}、"
               f"{'⏸ 已暂停' if s.get('paused') else '运行中'}。",
               f"预计剩余：{eta_s}。"]
        items = s.get("items") or []
        if items:
            out.append("当前在深索/队首：")
            for it in items[:8]:
                out.append(f"- {it.get('title') or it.get('key')}")
        return "\n".join(out)
    if name == "deep_index":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        body = {"batch": args.get("batch", 15)}
        sm = args.get("summaries")
        if sm:
            body["summaries"] = [{"key": x.get("key", ""), "summary": x.get("summary", "")}
                                 for x in sm if x.get("key")]
        r = requests.post(URL + "/index/deep_agent", json=body, timeout=1800).json()
        if r.get("busy"):
            return "知识库正在建库/深索中（有其它构建任务在跑），请稍后再调用 deep_index。"
        if not r.get("ok"):
            # BF16：后端子阶段失败返回 {ok:false,error,stage}——把人话透传给 agent，
            # 否则 agent 拿不到真因会向用户误报「已嵌入入库」。
            return "深索失败：" + str(r.get("error") or r.get("detail") or r)
        done, wp, rem = r.get("done"), r.get("with_pdf"), r.get("remaining")
        ts = r.get("to_summarize") or []
        if r.get("finished") and not ts:
            tail = f"（本次已把 {r.get('wrote')} 篇摘要嵌入入库）" if r.get("wrote") else ""
            return f"✅ 深索全部完成。已深索 {done}/{wp} 篇（有PDF），无更多待处理。{tail}"
        out = []
        if r.get("wrote"):
            out.append(f"已把上一批 {r.get('wrote')} 篇摘要嵌入入库。")
        out.append(f"进度：已深索 {done}/{wp} 篇，剩余约 {rem} 篇待写摘要。")
        out.append(f"请给下面 {len(ts)} 篇各写一段约150字中文检索摘要，"
                   f"然后带 summaries=[{{key, summary}}] 再次调用 deep_index：\n")
        for i, x in enumerate(ts, 1):
            out.append(f"[{i}] key: {x.get('key')}")
            out.append(f"    标题：{x.get('title')}")
            ex = (x.get("excerpt") or "").strip().replace("\n", " ")
            out.append(f"    正文节选：{ex[:1200]}" if ex else "    正文节选：（无可抽文本，可能是扫描件，可跳过此篇不写摘要）")
        return "\n".join(out)
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
        # EN-M2：分页——后端 /wiki/list 已支持 offset/limit 并返回 total（契约10）
        off, lim = int(args.get("offset", 0) or 0), int(args.get("limit", 100) or 100)
        r = requests.get(URL + "/wiki/list", params={"offset": off, "limit": lim}, timeout=30).json()
        pages = r.get("pages", [])
        total = r.get("total", len(pages))   # 老后端无 total 时兜底本页条数
        if not pages:
            return ("综合层还没有任何 wiki 页（可用 save_synthesis 写回第一条）。" if off == 0
                    else f"offset={off} 超出范围（共 {total} 条）。")
        out = [f"综合层共 {total} 页，本页 offset={off}、返回 {len(pages)} 页（动手前先看有没有现成的）："]
        for p in pages:
            flag = "🤖未核验" if p.get("by_agent") else "🧑"
            stale = "·⚠过时" if p.get("stale") else ""
            out.append(f"- [{p.get('id')}] {p.get('kind')}{stale} {flag} {p.get('title', '')}"
                       f"（基于 {p.get('n_sources', 0)} 篇 · {str(p.get('generated_at', ''))[:10]}）")
        if off + len(pages) < total:
            out.append(f"…… 还有 {total - off - len(pages)} 页未列出，续取请传 offset={off + len(pages)}。")
        return "\n".join(out), {"total": total, "offset": off, "pages": pages}
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

    # ── 读原文（ingest 地基）──
    if name == "read_source":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        key = str(args.get("key", "")).strip()
        if not key:
            return "需要 key（论文标识）。可从 search_localkb 结果的 «key:…» 或 list_sources 取得。"
        resp = requests.get(URL + "/source/" + key,
                            params={"from_page": args.get("from_page", 1),
                                    "to_page": args.get("to_page", 0),
                                    "max_chars": args.get("max_chars", 20000)}, timeout=90)
        if resp.status_code == 404:
            return f"知识库里没有 key={key} 的文献。先用 list_sources 或 search_localkb 确认 key。"
        if resp.status_code != 200:
            return f"读取失败：{_err_of(resp)}"
        r = resp.json()
        if not r.get("ok"):
            return f"读不到这篇的全文（{r.get('reason')}）：{r.get('detail', '')}"
        head = (f"《{r.get('title', '')}》 {r.get('author', '')} {r.get('year', '')} {r.get('journal', '')}\n"
                f"全文共 {r.get('n_pages_total')} 页；本次返回 {r.get('returned_pages')} 页 / {r.get('chars')} 字。")
        if r.get("truncated"):
            head += f"\n⚠ 已按 max_chars 截断——续读请传 from_page={r.get('next_page')}。"
        body = []
        for pg in r.get("pages", []):
            pp = pg.get("printed_page") or ""
            mark = f"—— PDF 第 {pg['pdf_page']} 页" + (f"（印刷页 {pp}）" if pp else "") + " ——"
            body.append(f"\n{mark}\n{pg.get('text', '')}")
        # EN-M3：结构化件只带元数据+页码映射、不重复全文——正文可达 2 万字，
        # structuredContent 若原样再带一份，token 直接翻倍，得不偿失。
        sc = {"key": key, "title": r.get("title"), "author": r.get("author"),
              "year": r.get("year"), "journal": r.get("journal"),
              "n_pages_total": r.get("n_pages_total"), "returned_pages": r.get("returned_pages"),
              "chars": r.get("chars"), "truncated": bool(r.get("truncated")),
              "next_page": r.get("next_page"),
              "pages": [{"pdf_page": pg.get("pdf_page"), "printed_page": pg.get("printed_page")}
                        for pg in r.get("pages", [])]}
        return head + "\n" + "".join(body), sc

    if name == "list_sources":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        # EN-M2：分页——/papers 已支持 offset/total，千篇大库终于能翻到后面
        off = int(args.get("offset", 0) or 0)
        params = {"limit": args.get("limit", 50), "offset": off}
        deep = args.get("deep", "all")
        if deep in ("yes", "no"):
            params["deep"] = deep
        if args.get("category"):
            params["category"] = args["category"]
        resp = requests.get(URL + "/papers", params=params, timeout=30)
        if resp.status_code != 200:
            return f"列举失败：{_err_of(resp)}"
        r = resp.json()
        items = r.get("papers", [])
        total = r.get("total", len(items))
        if not items:
            return ("没有符合条件的文献。" if off == 0
                    else f"offset={off} 超出范围（符合条件共 {total} 篇）。")
        scope = {"yes": "已深索", "no": "未深索", "all": "全部"}.get(deep, "全部")
        out = [f"{scope}文献：符合条件共 {total} 篇，本页 offset={off}、返回 {len(items)} 篇："]
        for p in items:
            flags = []
            if p.get("no_text"):
                flags.append("扫描件·不可读全文")
            if not p.get("has_pdf"):
                flags.append("仅题录·无PDF")
            tail = ("　[" + "；".join(flags) + "]") if flags else ""
            out.append(f"- «key:{p.get('key')}» {p.get('title', '')}"
                       f"（{p.get('author', '')} {p.get('year', '')}，{p.get('journal', '')}"
                       f"{'·' + p['weight_tier'] if p.get('weight_tier') else ''}）{tail}")
        if off + len(items) < total:
            out.append(f"…… 还有 {total - off - len(items)} 篇未列出，续取请传 offset={off + len(items)}。")
        return "\n".join(out), {"total": total, "offset": off, "papers": items}

    # ── lint 地基：标脏 + 反查 ──
    if name == "mark_stale":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        pid = str(args.get("page_id", "")).strip()
        if not pid:
            return "需要 page_id（来自 list_wiki）。"
        stale = args.get("stale", True)
        resp = requests.post(URL + "/wiki/stale/" + pid,
                             json={"stale": bool(stale), "reason": args.get("reason", "")}, timeout=30)
        if resp.status_code == 404:
            return f"无此综合页：{pid}（先用 list_wiki 查 id）。"
        if resp.status_code != 200:
            return f"标记失败：{_err_of(resp)}"
        r = resp.json()
        if stale:
            return (f"已把「{r.get('title')}」标为过时（检索中显著降权，界面显示 ⚠ 徽标）。"
                    f"理由：{r.get('reason') or '（未填）'}")
        return f"已清除「{r.get('title')}」的过时标记。"

    if name == "get_backlinks":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        key, pid = args.get("key"), args.get("page_id")
        if not key and not pid:
            return "需要 key（论文）或 page_id（综合页）之一。"
        resp = requests.get(URL + "/wiki/backlinks",
                            params={k: v for k, v in (("key", key), ("page_id", pid)) if v}, timeout=30)
        if resp.status_code != 200:
            return f"反查失败：{_err_of(resp)}"
        r = resp.json()
        if key:
            cb = r.get("cited_by", [])
            if not cb:
                return f"没有任何综合页引用论文 {key}。（若刚读完这篇，可考虑写一页综合。）"
            out = [f"引用了论文 {key} 的综合页共 {len(cb)} 个——新增/更新这篇后，逐一判断是否需要 mark_stale 或重生："]
            for p in cb:
                out.append(f"- [{p['id']}] {p['kind']} {p['title']}{'（已标过时）' if p.get('stale') else ''}")
            return "\n".join(out)
        out = [f"综合页「{r.get('title')}」（{pid}）："]
        out.append(f"- 引用论文 {len(r.get('sources', []))} 篇：" +
                   "、".join((s.get("citation") or s.get("key", ""))[:40] for s in r.get("sources", [])[:8]))
        out.append(f"- 出链到 {len(r.get('links_out', []))} 页；被 {len(r.get('links_in', []))} 页链入")
        if r.get("orphan"):
            out.append("- ⚠ 这是一个**孤儿页**：既不链出、也无人链入。考虑给它补互链。")
        return "\n".join(out)

    # ── 维护权 ──
    if name == "update_wiki_page":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        pid = str(args.get("page_id", "")).strip()
        if not pid:
            return "需要 page_id。新建时自取，建议带类型前缀（entity-xxx / concept-xxx / overview-main）。"
        body = {"kind": args.get("kind"), "title": args.get("title"),
                "content": args.get("content", ""),
                "sources": [{"key": k} for k in (args.get("sources") or [])],
                "mode": args.get("mode", "replace"), "links": args.get("links"),
                "by_agent": True, "model": "agent"}
        resp = requests.post(URL + "/wiki/page/" + pid, json=body, timeout=120)
        if resp.status_code == 409:
            return "拒绝写入：" + _err_of(resp)
        if resp.status_code != 200:
            return "写入失败：" + _err_of(resp)
        r = resp.json()
        state = "已入表可检索" if r.get("indexed") else "已存盘"
        tail = f"，互链 {len(r.get('links') or [])} 条" if r.get("links") else ""
        return (f"已{'追加到' if args.get('mode') == 'append' else '写入'}「{r.get('title')}」"
                f"（id={r.get('id')}，kind={r.get('kind')}，{state}，引用 {r.get('n_sources')} 篇{tail}）。")

    if name == "set_wiki_links":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        pid = str(args.get("page_id", "")).strip()
        resp = requests.post(URL + "/wiki/links/" + pid,
                             json={"links": args.get("links") or [], "mode": args.get("mode", "replace"),
                                   "by_agent": True},   # 时间线标注：这是 agent 动的图
                             timeout=30)
        if resp.status_code != 200:
            return "写互链失败：" + _err_of(resp)
        r = resp.json()
        msg = f"「{pid}」现有 {len(r['links'])} 条互链：{'、'.join(r['links']) or '（无）'}"
        if r.get("skipped"):
            msg += f"\n⚠ 已跳过 {len(r['skipped'])} 个无效目标（自链或不存在的页）：{'、'.join(r['skipped'])}"
        return msg

    # ── gist 的 Lint 与 Ingest 编排 ──
    if name == "lint_wiki":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        resp = requests.get(URL + "/wiki/lint", params={"min_mentions": args.get("min_mentions", 2)}, timeout=60)
        if resp.status_code != 200:
            return "体检失败：" + _err_of(resp)
        r = resp.json()
        if r.get("healthy"):
            return f"综合层健康（共 {r['n_pages']} 页）：无孤儿页、无过时页、无断链、无缺 provenance 的页。"
        iss = r["issues"]
        out = [f"综合层体检：{r['n_pages']} 页，发现 {r['n_issues']} 个问题。\n"]
        # body_broken_link（正文 [[wikilink]] 断链）是后端 lint 必返回的第 7 类；旧字典漏配它，
        # 一旦任何页正文有断链（agent 先引后建是常态）就 KeyError，整份体检报告崩掉。
        label = {"orphan": "孤儿页（无任何互链）", "stale": "已标过时", "broken_link": "断链",
                 "body_broken_link": "正文互链指向不存在的页",
                 "no_sources": "无来源论文", "degraded": "降级页（未配 AI 模型时生成）",
                 "missing_concept": "被反复提及却无独立页的概念"}
        for k, items in iss.items():
            if not items:
                continue
            out.append(f"■ {label.get(k, k)}（{len(items)}）：")
            for x in items[:8]:
                if k in ("broken_link", "body_broken_link"):  # 二者条目同构：page_id/title/dangling
                    out.append(f"   - [{x['page_id']}] {x['title']} → 指向不存在的 {x['dangling']}")
                elif k == "missing_concept":
                    out.append(f"   - 「{x['concept']}」被 {x['mentioned_in']} 个页提及")
                else:
                    extra = f"（{x['reason']}）" if x.get("reason") else ""
                    out.append(f"   - [{x['id']}] {x['title']}{extra}")
            if len(items) > 8:
                out.append(f"   …… 还有 {len(items) - 8} 个")
        out.append("\n建议动作：")
        out += [f"  · {s}" for s in r.get("suggestions", [])]
        return "\n".join(out)

    if name == "propose_wiki_updates":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        key = str(args.get("key", "")).strip()
        resp = requests.get(URL + "/wiki/propose/" + key, params={"topk": args.get("topk", 12)}, timeout=90)
        if resp.status_code != 200:
            return "分析失败：" + _err_of(resp)
        r = resp.json()
        out = [f"论文 {key} 触及 {r['n_affected']} 个既有综合页：\n"]
        for a in r.get("affected", []):
            rel = "直接引用了这篇" if a["relation"] == "cites_this_source" else "同主题但未引用这篇"
            flag = "（已标过时）" if a.get("stale") else ""
            out.append(f"■ [{a['id']}] {a['kind']} · {a['title']}{flag}\n   关系：{rel}\n   建议：{a['action']}")
        for h in r.get("hints", []) or [r.get("note", "")]:
            if h:
                out.append(f"\n提示：{h}")
        return "\n".join(out)

    if name == "pending_wiki_updates":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        resp = requests.get(URL + "/wiki/suggestions", timeout=30)
        if resp.status_code != 200:
            return "读取待办失败：" + _err_of(resp)
        items = (resp.json() or {}).get("items") or []
        if not items:
            return "当前没有待处理的 wiki 更新建议（最近没有新深索的文献，或都已处理）。"
        out = [f"有 {len(items)} 篇新文献可能影响既有综合页，请逐一处理：\n"]
        for it in items[:30]:
            title = it.get("title") or it.get("key") or "?"
            pages = it.get("pages") or []
            plist = "、".join(f"[{p.get('id','')}] {p.get('title','')}" for p in pages) or "（无具体页，深索后自查）"
            out.append(f"■ {title}（key={it.get('key','')}）\n   可能影响：{plist}")
        out.append("\n处理：对每页 get_wiki_page 看结论是否仍成立；被推翻→mark_stale + update_wiki_page；仍成立→跳过。"
                   "处理完可用 /wiki/suggestions/dismiss（或在应用里）清掉该条。")
        return "\n".join(out)

    # ── EN-M1：论文写作工作流工具 ──────────────────────────────
    if name == "format_citation":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        key = str(args.get("key", "")).strip()
        if not key:
            return "需要 key（文献标识，来自 search_localkb / list_sources）。"
        params = {"style": args.get("style", "footnote")}
        if args.get("pdf_page") is not None:
            params["page"] = args["pdf_page"]
        resp = requests.get(URL + "/cite/" + key, params=params, timeout=30)
        if resp.status_code == 404:
            return f"知识库里没有 key={key} 的文献。先用 list_sources 或 search_localkb 确认 key。"
        if resp.status_code != 200:
            return f"排注失败：{_err_of(resp)}"
        r = resp.json()
        out = [r.get("formatted", "")]
        if r.get("page_estimated"):
            out.append("⚠ 页码为连续性推算值，请对照原文核对（可用 locate_quote 定位原句确认）。")
        mf = r.get("missing_fields") or []
        if mf:
            out.append("⚠ 题录缺字段：" + "、".join(mf) + "——引注可能不完整，请人工补齐。")
        out.append("（引领词「参见/见/转引自」由作者按引用性质自定，本工具不加。）")
        return "\n".join(out)

    if name == "get_source_meta":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        key = str(args.get("key", "")).strip()
        if not key:
            return "需要 key（文献标识）。"
        resp = requests.get(URL + "/paper/" + key, timeout=30)
        if resp.status_code == 404:
            return f"知识库里没有 key={key} 的文献。先用 list_sources 或 search_localkb 确认 key。"
        if resp.status_code != 200:
            return f"读取题录失败：{_err_of(resp)}"
        p = resp.json()
        flags = []
        if p.get("deep"):
            flags.append("已深索·可 read_source 读全文")
        elif p.get("has_pdf"):
            flags.append("有PDF·未深索")
        else:
            flags.append("仅题录·无PDF")
        if p.get("no_text"):
            flags.append("扫描件·不可抽文本")
        if p.get("statute_status"):
            flags.append("法条时效：" + p["statute_status"])
        out = [f"《{p.get('title', '')}》 «key:{key}»",
               f"{p.get('author', '')}，{p.get('journal', '')}，{p.get('year', '')}"
               f"（{p.get('itemtype', '')}{'·' + p['weight_tier'] if p.get('weight_tier') else ''}）",
               f"官方页码：{p.get('official_pages') or '未知'}　收藏夹：{'、'.join(p.get('collections') or []) or '（无）'}",
               f"状态：{'；'.join(flags)}　入库：{str(p.get('ingested_at', ''))[:10]}"]
        if p.get("abstract"):
            out.append("摘要：" + str(p["abstract"])[:500])
        cb = p.get("cited_by_wiki") or []
        if cb:
            out.append(f"被 {len(cb)} 个综合页引用：" + "、".join(f"[{w.get('id')}] {w.get('title', '')}" for w in cb[:10]))
        else:
            out.append("尚无综合页引用这篇。")
        return "\n".join(out), p   # EN-M3：整份题录作 structuredContent

    if name == "similar_sources":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        key = str(args.get("key", "")).strip()
        if not key:
            return "需要 key（种子文献标识）。"
        resp = requests.get(URL + "/similar/" + key, params={"topk": args.get("topk", 8)}, timeout=60)
        if resp.status_code != 200:
            return f"找相似失败：{_err_of(resp)}"
        r = resp.json()
        if not r.get("ok"):
            return ("这篇暂时算不了向量相似（需要语义索引 full 模式、且该篇已入向量表）。"
                    "可先 localkb_build(stage='semantic') 建语义索引，或改用 search_localkb 换关键词检索。")
        res = r.get("results") or []
        if not res:
            return f"没有找到与 {key} 相似的其它文献。"
        out = [f"与 «key:{key}» 向量最相近的 {len(res)} 篇（可据此扩展检索面）："]
        for i, x in enumerate(res, 1):
            tag = "📝综合" if x.get("is_wiki") else x.get("journal_tier")
            out.append(f"[{i}] ({tag}) {x.get('citation')}  «key:{x.get('key', '')}»")
        return "\n".join(out)

    if name == "whats_new":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        days = max(1, int(args.get("days", 7) or 7))
        limit = max(1, int(args.get("limit", 20) or 20))
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = requests.get(URL + "/papers",
                            params={"sort": "ingested", "since": since, "limit": limit}, timeout=30)
        if resp.status_code != 200:
            return f"查询失败：{_err_of(resp)}"
        items = resp.json().get("papers", [])
        # 双保险：后端 since 未生效时（老版本），按返回的 ingested_at 在客户端再筛一遍
        items = [p for p in items if str(p.get("ingested_at", ""))[:10] >= since][:limit]
        if not items:
            return f"最近 {days} 天没有新入库的文献。"
        out = [f"最近 {days} 天新入库 {len(items)} 篇（按入库时间倒序）："]
        for p in items:
            out.append(f"- «key:{p.get('key')}» {p.get('title', '')}"
                       f"（{p.get('author', '')} {p.get('year', '')}，{p.get('journal', '')}"
                       f"，入库 {str(p.get('ingested_at', ''))[:10]}）")
        out.append("想知道这些新篇动了哪些综合页？对关心的篇逐一调 propose_wiki_updates / get_wiki_page。")
        # affected_pages 恒为空数组：对每篇现跑 propose 太贵，语义在工具描述里已申明
        return "\n".join(out), {"since": since, "days": days,
                                "new_papers": items, "affected_pages": []}

    if name == "locate_quote":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        quote = str(args.get("quote", "")).strip()
        if not quote:
            return "需要 quote（要核对的引文原句）。"
        body = {"quote": quote, "key": args.get("key") or None,
                "fuzzy": bool(args.get("fuzzy", True))}
        resp = requests.post(URL + "/research/locate_quote", json=body, timeout=120)
        if resp.status_code != 200:
            return f"定位失败：{_err_of(resp)}"
        r = resp.json()
        ms = r.get("matches") or []
        if not ms:
            scope = f"在 {args.get('key')} 里" if args.get("key") else "全库"
            return (f"{scope}没有找到这句话。可能：引文转述过、OCR 差异过大、或该篇未深索。"
                    f"建议 read_source 人工比对，或放宽引文长度重试。核对不过的引注请勿照抄。")
        out = [f"引文命中 {r.get('n', len(ms))} 处："]
        for i, m in enumerate(ms, 1):
            ex = "逐字一致" if m.get("exact") else "模糊命中（请人工比对）"
            pp = m.get("printed_page")
            out.append(f"[{i}] «key:{m.get('key')}» PDF 第 {m.get('pdf_page')} 页"
                       + (f"（印刷页 {pp}）" if pp else "") + f" · {ex}")
            ctx = (m.get("context") or "").strip().replace("\n", " ")
            if ctx:
                out.append(f"    上下文：…{ctx[:200]}…")
        out.append("排脚注可接 format_citation(key, pdf_page)。")
        return "\n".join(out), r   # EN-M3：契约6 原样作 structuredContent

    if name == "verify_claim":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        claim = str(args.get("claim", "")).strip()
        if not claim:
            return "需要 claim（要核验的论断）。"
        body = {"claim": claim, "keys": args.get("keys") or None,
                "topk": args.get("topk", 8)}
        resp = requests.post(URL + "/research/verify_claim", json=body, timeout=180)
        if resp.status_code != 200:
            return f"核验失败：{_err_of(resp)}"
        r = resp.json()
        v = r.get("verdict", "")
        head = {"supported": "✅ supported——库内证据支持该论断",
                "mismatch": "⚠ mismatch——库内证据与论断相左（可能记错来源或过度概括）",
                "not_in_lib": "❔ not_in_lib——库里找不到依据（**不等于论断为假**；"
                              "该论断要么删、要么明确标注「作者观点/库外知识」）"}.get(v, f"verdict={v}")
        out = [f"论断：{claim}", head + f"（置信度 {r.get('confidence', 0):.2f}）"]
        if r.get("note"):
            out.append("说明：" + str(r["note"]))
        ev = r.get("evidence") or []
        if ev:
            out.append("证据：")
            for e in ev[:8]:
                pp = e.get("printed_page")
                out.append(f"- «key:{e.get('key')}» PDF 第 {e.get('pdf_page')} 页"
                           + (f"（印刷页 {pp}）" if pp else "")
                           + f"：「{(e.get('quote') or '').strip()[:160]}」")
        return "\n".join(out), r   # EN-M3：契约7 原样作 structuredContent

    if name == "add_source":
        if not ensure_up():
            return "错误：知识库服务启动失败。"
        path = str(args.get("path", "")).strip()
        if not path:
            return "需要 path（PDF 的本机绝对路径）。"
        resp = requests.post(URL + "/ingest/local_path",
                             json={"path": path, "note": args.get("note") or None}, timeout=300)
        if resp.status_code != 200:
            # zotero 模式返回 400，detail 已是人话（「Zotero 模式请把 PDF 附到 Zotero 条目上」）
            return f"收录失败：{_err_of(resp)}"
        r = resp.json()
        if not r.get("ok"):
            return "收录失败：" + str(r.get("hint") or r.get("detail") or r)
        if r.get("status") == "duplicate":
            return f"这份 PDF 已在库里（key={r.get('key')}），未重复收录。"
        out = [f"已收进知识库（key={r.get('key')}）。题录由 AI 自动抽取，**待人工核对**（应用里标「待确认」）。"]
        if r.get("building"):
            out.append("建库在后台进行中，稍后可用 localkb_status / deep_status 查进度。")
        if r.get("hint"):
            out.append(str(r["hint"]))
        return "\n".join(out)

    return f"未知工具：{name}"


# ══ resources / prompts 的读取实现 ══
def read_resource(uri):
    if uri == "localkb://schema":
        return _wiki_schema_text() or "（WIKI.md 尚未生成）", "text/markdown"
    if not ensure_up():
        raise RuntimeError("知识库服务启动失败")
    if uri == "localkb://index":
        r = requests.get(URL + "/wiki/list", timeout=30).json()
        return json.dumps(r, ensure_ascii=False, indent=1), "application/json"
    if uri == "localkb://lint":
        r = requests.get(URL + "/wiki/lint", timeout=60).json()
        return json.dumps(r, ensure_ascii=False, indent=1), "application/json"
    if uri.startswith("localkb://page/"):
        pid = uri[len("localkb://page/"):]
        resp = requests.get(URL + "/wiki/page/" + pid, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"无此综合页：{pid}")
        return resp.json().get("markdown", ""), "text/markdown"
    raise RuntimeError(f"未知资源 uri：{uri}")

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
        if not isinstance(req, dict):   # 非对象 JSON 行（数组/字符串/数字…）：req.get 会 AttributeError 崩掉整个进程
            continue
        m, rid = req.get("method"), req.get("id")
        if m == "initialize":
            # 技能自动装（仅 Claude Code；不覆盖用户改过的）——放在回 instructions 前，失败不影响握手
            try:
                _maybe_install_skill((req.get("params", {}).get("clientInfo", {}) or {}).get("name", ""))
            except Exception:
                pass
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTO,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "localkb", "version": "1.2.0"},
                "instructions": instructions()}})
        elif m == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif m == "tools/call":
            try:
                out = do_tool(req["params"]["name"], req["params"].get("arguments", {}))
                # EN-M3：do_tool 返回 (text, dict) 时附 structuredContent（MCP 2025-06 规范：
                # 与 content[].text 并存，老客户端不认识该键、自然忽略，无破坏性）。文本仍是主载体。
                text, sc = out if isinstance(out, tuple) else (out, None)
                result = {"content": [{"type": "text", "text": text}]}
                if isinstance(sc, dict):
                    result["structuredContent"] = sc
                send({"jsonrpc": "2.0", "id": rid, "result": result})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"content": [{"type": "text", "text": "错误：" + str(e)}], "isError": True}})
        elif m == "resources/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resources": RESOURCES}})
        elif m == "resources/templates/list":
            # EN-M4：声明 localkb://page/{id} 模板——read_resource 早已实现，声明后 agent 才发现得了
            send({"jsonrpc": "2.0", "id": rid, "result": {"resourceTemplates": RESOURCE_TEMPLATES}})
        elif m == "resources/read":
            try:
                uri = req["params"]["uri"]
                text, mime = read_resource(uri)
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32602, "message": str(e)}})
        elif m == "prompts/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"prompts": PROMPTS}})
        elif m == "prompts/get":
            try:
                pname = req["params"]["name"]
                text = prompt_text(pname, req["params"].get("arguments", {}))
                desc = next((p["description"] for p in PROMPTS if p["name"] == pname), pname)
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "description": desc,
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(e)}})
        elif m == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif m and m.startswith("notifications/"):
            pass  # 通知类无需回复
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found: " + str(m)}})

if __name__ == "__main__":
    main()
