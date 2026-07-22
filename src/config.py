# -*- coding: utf-8 -*-
"""
本地知识库应用 —— 独立配置（中央路径/参数）。

⚠️ 独立性保证（与开发机上那个旧 rag 知识库目录的关系；路径由 LOCALKB_MODELS 指定，代码里不写死）：
  - 本项目所有【写入】都落在 DATA / LOGS 之下（见下方 DATA 解析：分发版=%LOCALAPPDATA%\\PaperPiggy\\data，
    源码态=源码目录 data/，可被 LOCALKB_DATA / LOCALKB_HOME 覆盖），**绝不写入模型或知识库目录**。
  - 模型【只读复用】知识库已下载好的 ONNX/hub（省去重新下 12GB），可用环境变量改指别处。
  - 数据源直接读 Zotero 的 zotero.sqlite（不依赖 Better BibTeX 导出）。
  - daemon 端口用 8770（知识库 daemon 是 8765），两者可同时运行、互不干扰。
所有引擎脚本统一 `import config as C`，改路径只改这一处。
"""
import os, sys
from pathlib import Path

# ★ 无窗铁律（CLAUDE.md §0.5）：所有 subprocess 在 Windows 上都带这个 flag，绝不闪控制台黑窗。
#   子进程用 pythonw + 此 flag 双保险。加任何拉起进程的新功能，creationflags 默认写 C.SUBPROC_NO_WINDOW。
SUBPROC_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW

# ---- 应用版本（唯一事实源）----
# 全项目**只有这一处**版本字面量：发版改版本号只改这里，其余地方一律 `C.APP_VERSION` 引用
# （踩过的坑：版本号散落在 mcp_server 的 serverInfo、打包脚本、页脚里，改一处漏三处，
#  用户报 bug 时报的版本对不上代码）。1.0.0 = 首个公开发布版（Apache-2.0 开源）。
APP_VERSION = "1.0.35"

APP = Path(__file__).parent                 # 源码目录；分发版 = bundle/app
RAG = APP                                    # 兼容：引擎脚本都在项目根

# ---- 分发包路径引导（BLOCKER 修复）----
# MCP / CLI 由 Claude Code 直接拉起 mcp_server.py / localkb.py 时不经过启动器，
# 进程 env 里没有 LOCALKB_DATA/LOCALKB_MODELS。若不在此补上：DATA 会错落到 app/data
# （会被升级安装覆盖掉），MCP/CLI 看到空库、拉起的假 server 占住 8770，
# 与用户随后打开的真应用“数据脑裂”。开发机（源码目录，其上一级探测不到 bundle 结构）
# 原样跳过，数据仍落在源码 data/。
# ★ 这里是 HOME 解析的**唯一实现**。run_localkb.py 曾经自己复刻了一份，两处各算各的 ——
#   典型的漂移种子（改一处忘一处 = 启动器和 MCP 认两个不同的数据目录）。现在启动器
#   只是 `import config` 借道过来。别再把这段逻辑复制出去。


def _writable(d):
    """能不能真往这儿写。装到 Program Files 时包内只读，必须探出来而不是假设。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".write_test"
        t.write_text("x", encoding="utf-8")
        t.unlink()
        return True
    except Exception:
        return False


# 数据家的兜底位置：%LOCALAPPDATA%\PaperPiggy（1.0.0 起。此前叫 LocalKB —— 门面已全面
# 改名，数据文件夹却还叫旧名，用户备份/找库/清卸载残留时会一脸问号，所以跟着改）。
# 不写 LocalKB→PaperPiggy 迁移：1.0.0 尚未发布，没有外部用户，开发机那份旧数据自己删。
# ⚠️ 环境变量 LOCALKB_* 刻意**不改**：只有开发者和 MCP 配置碰得到，改了要牵动
#    CLAUDE.md / launch.json / MCP接入说明 / 一堆 .py，对用户零收益。
def _user_home():
    env = os.environ.get("LOCALKB_HOME")
    if env:
        return Path(env)                         # 用户显式指定，尊重之
    # 各平台的用户数据惯例位置。Windows：%LOCALAPPDATA%\PaperPiggy；
    # macOS：~/Library/Application Support/PaperPiggy；Linux：$XDG_DATA_HOME 或 ~/.local/share。
    # （此前只有 Windows 分支，且兜底写死 '~\\AppData\\Local' —— 在 mac/Linux 上会产出含字面反斜杠
    #   的垃圾路径。给朋友的 macOS 源码版加分支，Windows 行为一字不变。）
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support")) / "PaperPiggy"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(appdata) / "PaperPiggy"
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(xdg) / "PaperPiggy"


def _bootstrap_bundle_env():
    if os.environ.get("LOCALKB_DATA"):
        return                                   # 已由启动器/用户设定，尊重之
    root = APP.parent                            # 分发版=bundle/ ；开发机=项目根
    is_bundle = ((root / "run_localkb.py").exists()
                 or (root / "python" / "python.exe").exists()
                 or (root / "portable.txt").exists())
    if not is_bundle:
        return                                   # 开发机：让 DATA 默认落在源码目录 data/

    # portable.txt =「数据与程序同在一个文件夹」，安装器版**默认带它**（产品决定）：
    # 用户可以把整个应用连数据带模型装到 D:\PaperPiggy，一个文件夹搬走，C 盘一点不占。
    # 升级由安装器做（只覆盖 app\ 和 python\，不碰 data\），所以不像手动解压 zip 那样
    # 会把索引删光 —— 这也是 1.0.0 砍掉便携 zip、只发安装器的原因。
    # ⚠️ 但安装向导允许用户把目录改到 Program Files，那里普通权限写不进去，首次建库必崩。
    #    所以**探测可写性**，写不进去就回退 %LOCALAPPDATA% —— 宁可占点 C 盘，也不能崩。
    home = None
    if (root / "portable.txt").exists():
        if _writable(root / "data"):
            home = root
        else:
            print(f"[config] 安装目录不可写（{root}），数据回退到用户目录",
                  file=sys.stderr, flush=True)
    if home is None:
        home = _user_home()

    try:
        (home / "data").mkdir(parents=True, exist_ok=True)
        (home / "models").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    os.environ.setdefault("LOCALKB_DATA", str(home / "data"))
    # 模型优先用包内 models/（--slim-models 打进包 或 首启已下载都落这里）；否则退回 HOME/models
    bundled = root / "models"
    if (bundled / "bge-m3-onnx" / "model_quantized.onnx").exists():
        os.environ.setdefault("LOCALKB_MODELS", str(bundled))
    else:
        os.environ.setdefault("LOCALKB_MODELS", str(home / "models"))

_bootstrap_bundle_env()

# ---- 独立数据（本项目自有，随便删不影响知识库）----
# LOCALKB_DATA 环境变量可把数据目录挪出 APP（分发版指向 bundle/data），
# 这样自动更新替换 app/ 时不会误删用户已建的索引。
DATA        = Path(os.environ.get("LOCALKB_DATA", str(APP / "data")))
EXTRACTED   = DATA / "extracted"            # 每篇 <safe_key>.json（提取的逐页文本）
CHUNKS      = DATA / "chunks"               # 每篇 <safe_key>.json（切块）
LANCEDB_DIR = DATA / "lancedb"              # 独立向量库
BM25_DIR    = DATA / "bm25"                 # 独立词法索引
STATE       = DATA / "state"               # 增量进度（embedded_keys.txt）
# 日志跟随 DATA（写在可写的用户数据区）：放 app/logs 会在自动更新替换 app/ 时丢失，
# 且装到 Program Files 等只读位置时 app/logs 不可写会导致启动写日志即崩。
LOGS        = DATA / "logs"
LEGAL_DICT  = DATA / "jieba_legal_dict.txt" # extract 会重新生成到这里（不碰知识库的词典）

# ---- 三档渐进索引（产品版）新增路径 ----
PAGEMAP_DIR       = DATA / "pagemap"        # 研究助手：PDF顺序页→期刊印刷页码映射 sidecar
FOLDER_DIR_STATE  = DATA / "folder"         # 文件夹模式 sidecar 根
FOLDER_META_CACHE = FOLDER_DIR_STATE / "meta_cache.json"  # {key:{meta,file,sha1,needs_review,extracted_at}}（BF10：存 sha1 不存 mtime，同路径文件替换靠 sha1 检测）
META_DIR       = DATA / "meta"              # L档：papers.jsonl（每行一篇全字段，含无全文的纯题录篇）
BM25_META_DIR  = DATA / "bm25_meta"         # L档：meta 文本的独立 bm25（0嵌入即时可搜）
CATEGORIES_DIR = DATA / "categories"        # 收藏夹/AI主题 sidecar
STATS_CACHE    = DATA / "stats_cache.json"  # 仪表盘 /stats 预聚合缓存
INDEX_MANIFEST = DATA / "index_manifest.json"  # 整库状态清单（各档进度/数据源）
PAPERS_JSONL   = META_DIR / "papers.jsonl"
META_EMBEDDED  = STATE / "meta_embedded.txt"   # S档已嵌入 meta 的 stem
ROW_TYPES = ("meta", "chunk", "wiki")   # +wiki：综合层页面进同表；wiki 行 chunk_id 以 "::wiki" 结尾

# ---- Agent 专属文件夹名（0_ 前缀排最前、人类可读；落点解析见 agent_ws.py）----
# 交付物=agent 成品；资料库=agent 干活要用的东西(记忆/技能/参考格式/交付模板/定时任务)。
# folder 模式建在受管文件夹内(folder_source.scan 已排除 0_Agent* 不入库)；zotero 模式落 DATA 同级。
AGENT_OUTPUT_NAME = "0_Agent交付物"
AGENT_RELY_NAME   = "0_Agent资料库"

# ---- 综合层 / wiki（答案沉淀 + 按需综述；只写 DATA/wiki，随便删不影响文献库/Zotero）----
WIKI_DIR          = DATA / "wiki"            # 综合页 markdown + index.json（sidecar，仿 categories/）
WIKI_ANSWERS_DIR  = WIKI_DIR / "answers"     # Phase 0：沉淀的问答综合 <id>.md
WIKI_CONCEPTS_DIR = WIKI_DIR / "concepts"    # Phase 1：概念页 <slug>.md
WIKI_TOPICS_DIR   = WIKI_DIR / "topics"      # Phase 1：主题页 <id>.md
WIKI_DIGEST_DIR   = WIKI_DIR / "digests"     # 研究助手：带页级引注的资料汇编 <id>.md
WIKI_OUTLINE_DIR  = WIKI_DIR / "outlines"    # 研究助手：选题/框架/大纲 <id>.md
# gist 反复点名、此前缺失的两个骨干页种：
WIKI_ENTITY_DIR   = WIKI_DIR / "entities"    # 实体页：作者/机构/案件/制度（随 ingest 增量加厚）
WIKI_OVERVIEW_DIR = WIKI_DIR / "overviews"   # 总论页：随全库演进的 thesis（每次 ingest 强化或挑战它）
WIKI_INDEX        = WIKI_DIR / "index.json"  # 页面清单 id→元数据（provenance/stale 命脉）
WIKI_SCHEMA_MD    = WIKI_DIR / "WIKI.md"     # 第3层 schema：页面约定/引用格式/写回纪律
WIKI_HISTORY_DIR  = WIKI_DIR / ".history"    # 无 git 时的版本快照兜底（见 wiki_vcs.py）

# ---- 模型路径解析（分发包内优先，回退开发机的知识库目录）----
# 优先级：环境变量 LOCALKB_MODELS > 包内相对目录(APP/models) > 开发机知识库目录。
# 这样开发机(模型在知识库)与分发包(模型下载到包内 models/)都能正确解析。
def _resolve_models():
    env = os.environ.get("LOCALKB_MODELS")
    if env:
        return Path(env)
    local = APP / "models"
    if (local / "bge-m3-onnx").exists():        # 分发包：模型已下载到包内
        return local
    parent = APP.parent / "models"              # 分发版模型在 bundle 根的 models/（app 的上一级）
    if (parent / "bge-m3-onnx").exists():
        return parent
    # 开发机回退：模型可能复用别处已下好的目录，用环境变量 LOCALKB_DEV_MODELS 指定，
    # 不再裸写某台开发机的绝对路径（换机/分发时那条路径无意义，且泄漏本机目录结构）。
    dev_env = os.environ.get("LOCALKB_DEV_MODELS")
    if dev_env:
        dev = Path(dev_env)
        if dev.exists():
            return dev
    return local                                 # 都无则指向包内(待首启下载)

MODELS = _resolve_models()

# 只创建“本项目自有”的目录；绝不 mkdir MODELS（那是知识库的，只读）
for _d in (EXTRACTED, CHUNKS, LANCEDB_DIR, BM25_DIR, STATE, LOGS,
           DATA / "summaries", META_DIR, BM25_META_DIR, CATEGORIES_DIR,
           FOLDER_DIR_STATE, PAGEMAP_DIR,
           WIKI_DIR, WIKI_ANSWERS_DIR, WIKI_CONCEPTS_DIR, WIKI_TOPICS_DIR,
           WIKI_DIGEST_DIR, WIKI_OUTLINE_DIR, WIKI_ENTITY_DIR, WIKI_OVERVIEW_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# HF 缓存指向复用的模型目录；离线加载，防联网 etag 检查
os.environ.setdefault("HF_HOME", str(MODELS))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ---- 模型 ----
EMBED_MODEL  = "BAAI/bge-m3"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
EMBED_DIM    = 1024

# ---- 切块参数（与知识库一致，保证行为相同）----
CHILD_MAX_CHARS     = 500
CHILD_OVERLAP_CHARS = 80
PARENT_MAX_CHARS    = 2400
MIN_CHUNK_CHARS     = 12

# ---- 检索参数 ----
DENSE_TOPK  = 50
BM25_TOPK   = 50
# BF6：有 keys 白名单（限定分类检索）时 dense/bm25 源头召回放大到此值——50 条粗召回
# 被白名单一过滤常常只剩个位数，池子先天不足。只在白名单场景放大，常规检索维持 50 不拖慢。
FILTER_SRC_TOPK = 150
RRF_K       = 60
RERANK_TOPK = 8
TABLE_NAME  = "chunks"
MAX_PER_KEY = 2     # 每篇文献最多几块进入最终 topk（按 key 去重保证来源多样；设 999=不去重）

# ---- daemon（端口 8770，避开知识库的 8765）----
DAEMON_HOST  = "127.0.0.1"
DAEMON_PORT  = 8770
DAEMON_URL   = f"http://{DAEMON_HOST}:{DAEMON_PORT}"

# ---- 期刊分级 ----
JOURNAL_TIERS_FILE = DATA / "journal_tiers.json"
DEFAULT_SORT = "blend"
TIER_BONUS = {
    "CLSCI": 0.5, "台湾核心": 0.5, "外文顶级法评": 0.5,
    "外文权威": 0.4,
    "CSSCI": 0.25, "台湾一般": 0.25,
    "CSSCI扩展": 0.1,
    "普刊": 0.0, "外文一般": 0.0, "境外": 0.0,
    "台湾": 0.25, "报纸": -0.3, "未知": 0.0,
    "法源": 0.46, "官方报告": 0.42,   # source_rules 定档（=0.92/0.85 × WEIGHT_BONUS_SCALE，与新引擎口径对齐）
}

# ---- 来源评价权重接入（journal_grading 引擎；检索期动态算，改学科即时生效、不用重建索引）----
# API reranker 返回 0~1，本地 ONNX 返回原始 logit（尺度更宽），不能再共用一把加成尺。
# API=0.30：由 38 条真实查询金标集确定（25 条调参、13 条盲测）；相较旧 0.50，
# 校准组 Recall@8 从 94.4% 恢复到 100%，盲测保持 Hit@3/Recall@8=100%，nDCG 也略升。
# 本地模式尚无同口径金标，保留既有 0.50，避免拿 API 结论硬套另一种分数尺度。
WEIGHT_BONUS_SCALE_API = 0.30
WEIGHT_BONUS_SCALE_LOCAL = 0.50
# 兼容外部脚本/旧插件读取；运行时 _blend_bonus 会按后端选上面两个明确常量。
WEIGHT_BONUS_SCALE = WEIGHT_BONUS_SCALE_LOCAL

# ---- 法学检索增强（EN 系列，2026-07）----
# EN-L3：查询侧同义词扩展开关。只扩 bm25 的**查询**词袋（索引侧一个字节不动→零重建成本；
# dense 语义向量本身能泛化同义词，不参与扩展）。出厂词表在 legal_lexicon.py，
# 用户可用 data/legal_synonyms.txt 叠加（每行一组、顿号/逗号分隔）。关掉即完全回旧行为。
SYN_EXPAND = True
LEGAL_SYNONYMS_FILE = DATA / "legal_synonyms.txt"
# EN-L5：已废止法条的降权因子。**乘性**且分正负域：正分乘、负分除（BF5 教训：reranker 分
# 可为负，负分×0.5 反而离 0 更近=反向提权）。已修订不降权、只输出 statute_status 徽标。
STATUTE_REPEALED_FACTOR = 0.5

# ---- 综合层检索排序（wiki 行是"附加缓存"，不喧宾夺主，符合 §0"provenance 居中"）----
# 新鲜综合页：**减法**小惩罚，只在同分时让位于原始文献（provenance 居中）。
WIKI_BASE_PENALTY  = 0.05
# 过时综合页：**乘法**重罚。此处必须是乘法——reranker 分尺度是 0~10+（实测一个 answer 页 7.99，
# 同题最相关的真论文才 4.34），减 0.5 根本拉不动它。而 answer 页的标题就是用户的原问题，
# reranker 拿 query 对 query 打分，分数天然虚高：agent 写回一页，下次同样的问题必然命中它自己
# 写的页排第一 —— 这正是幻觉复利的引擎。乘 0.3 才能让被推翻的旧综合真正沉到真论文之下。
WIKI_STALE_FACTOR  = 0.3
# BF5：answer 页（未 stale）的乘法折减。answer 页标题≈用户原查询，reranker 拿 query 对 query
# 打分会虚高 3 分+（实测 7.99 vs 真论文 4.34）。0.7 太温和（7.99×0.7=5.59 仍压过真论文 4.34），
# 调到 0.45（7.99×0.45≈3.6<4.34）才真正让 answer 页沉到最相关真论文之下，消除"幻觉复利引擎"。
WIKI_ANSWER_FACTOR = 0.45
# 可信度分层：agent 写回、且尚未经人工核验（verified_at 为空）的综合页——额外乘性折减。
# 这类页是"agent 上次自己写的、没核过的草稿"，最不该被下次检索当既有事实复用（by_agent 轴此前在
# 排序层完全无防护，是"过度信任"的核心缺口）。人工核验过的页（verified_at 有值）豁免此折减。
# 与 answer 折减可叠乘：未核验的 answer 页 = 0.45×0.6=0.27，压得最狠，正合其可信度最低。
WIKI_UNVERIFIED_FACTOR = 0.6
