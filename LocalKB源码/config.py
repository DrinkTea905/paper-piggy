# -*- coding: utf-8 -*-
"""
本地知识库应用 —— 独立配置（中央路径/参数）。

⚠️ 独立性保证（与 D:\\00Zotero知识库\\rag 的关系）：
  - 本项目所有【写入】都落在 D:\\LocalKB\\data 与 D:\\LocalKB\\logs，绝不写入知识库目录。
  - 模型【只读复用】知识库已下载好的 ONNX/hub（省去重新下 12GB），可用环境变量改指别处。
  - 数据源直接读 Zotero 的 zotero.sqlite（不依赖 Better BibTeX 导出）。
  - daemon 端口用 8770（知识库 daemon 是 8765），两者可同时运行、互不干扰。
所有引擎脚本统一 `import config as C`，改路径只改这一处。
"""
import os
from pathlib import Path

APP = Path(__file__).parent                 # D:\LocalKB（源码/程序目录；分发版=bundle/app）
RAG = APP                                    # 兼容：引擎脚本都在项目根

# ---- 独立数据（本项目自有，随便删不影响知识库）----
# LOCALKB_DATA 环境变量可把数据目录挪出 APP（分发版指向 bundle/data），
# 这样自动更新替换 app/ 时不会误删用户已建的索引。
DATA        = Path(os.environ.get("LOCALKB_DATA", str(APP / "data")))
EXTRACTED   = DATA / "extracted"            # 每篇 <safe_key>.json（提取的逐页文本）
CHUNKS      = DATA / "chunks"               # 每篇 <safe_key>.json（切块）
LANCEDB_DIR = DATA / "lancedb"              # 独立向量库
BM25_DIR    = DATA / "bm25"                 # 独立词法索引
STATE       = DATA / "state"               # 增量进度（embedded_keys.txt）
LOGS        = APP / "logs"
LEGAL_DICT  = DATA / "jieba_legal_dict.txt" # extract 会重新生成到这里（不碰知识库的词典）

# ---- 三档渐进索引（产品版）新增路径 ----
PAGEMAP_DIR       = DATA / "pagemap"        # 研究助手：PDF顺序页→期刊印刷页码映射 sidecar
FOLDER_DIR_STATE  = DATA / "folder"         # 文件夹模式 sidecar 根
FOLDER_META_CACHE = FOLDER_DIR_STATE / "meta_cache.json"  # {key:{meta,file,mtime,sha1,needs_review,extracted_at}}
META_DIR       = DATA / "meta"              # L档：papers.jsonl（每行一篇全字段，含无PDF篇）
BM25_META_DIR  = DATA / "bm25_meta"         # L档：meta 文本的独立 bm25（0嵌入即时可搜）
CATEGORIES_DIR = DATA / "categories"        # 收藏夹/AI主题 sidecar
STATS_CACHE    = DATA / "stats_cache.json"  # 仪表盘 /stats 预聚合缓存
INDEX_MANIFEST = DATA / "index_manifest.json"  # 整库状态清单（各档进度/数据源）
PAPERS_JSONL   = META_DIR / "papers.jsonl"
META_EMBEDDED  = STATE / "meta_embedded.txt"   # S档已嵌入 meta 的 stem
ROW_TYPES = ("meta", "chunk", "wiki")   # +wiki：综合层页面进同表；wiki 行 chunk_id 以 "::wiki" 结尾

# ---- 综合层 / wiki（答案沉淀 + 按需综述；只写 DATA/wiki，随便删不影响文献库/Zotero）----
WIKI_DIR          = DATA / "wiki"            # 综合页 markdown + index.json（sidecar，仿 categories/）
WIKI_ANSWERS_DIR  = WIKI_DIR / "answers"     # Phase 0：沉淀的问答综合 <id>.md
WIKI_CONCEPTS_DIR = WIKI_DIR / "concepts"    # Phase 1：概念页 <slug>.md
WIKI_TOPICS_DIR   = WIKI_DIR / "topics"      # Phase 1：主题页 <id>.md
WIKI_DIGEST_DIR   = WIKI_DIR / "digests"     # 研究助手：带页级引注的资料汇编 <id>.md
WIKI_OUTLINE_DIR  = WIKI_DIR / "outlines"    # 研究助手：选题/框架/大纲 <id>.md
WIKI_INDEX        = WIKI_DIR / "index.json"  # 页面清单 id→元数据（provenance/stale 命脉）
WIKI_SCHEMA_MD    = WIKI_DIR / "WIKI.md"     # 第3层 schema：页面约定/引用格式/写回纪律

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
    dev = Path(r"D:\00Zotero知识库\rag\data\models")
    return dev if dev.exists() else local        # 回退开发机；都无则指向包内(待首启下载)

MODELS = _resolve_models()

# 只创建“本项目自有”的目录；绝不 mkdir MODELS（那是知识库的，只读）
for _d in (EXTRACTED, CHUNKS, LANCEDB_DIR, BM25_DIR, STATE, LOGS,
           DATA / "summaries", META_DIR, BM25_META_DIR, CATEGORIES_DIR,
           FOLDER_DIR_STATE, PAGEMAP_DIR,
           WIKI_DIR, WIKI_ANSWERS_DIR, WIKI_CONCEPTS_DIR, WIKI_TOPICS_DIR,
           WIKI_DIGEST_DIR, WIKI_OUTLINE_DIR):
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
RRF_K       = 60
RERANK_TOPK = 8
TABLE_NAME  = "chunks"
MAX_PER_KEY = 2     # 每篇文献最多几块进入最终 topk（按 key 去重保证来源多样；设 999=不去重）

# ---- daemon（端口 8770，避开知识库的 8765）----
DAEMON_HOST  = "127.0.0.1"
DAEMON_PORT  = 8770
IDLE_TIMEOUT = 1800
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
}

# ---- 期刊权重接入（journal_grading 引擎；检索期动态算，改学科即时生效、不用重建索引）----
# blend 排序里 journal_weight∈[0,1] 的加成尺度：weight=1.0(T1)→+0.5，与旧 CLSCI 的 TIER_BONUS 齐平。
# journal_grading 不可用/算不出时，_apply_sort 自动回退到上面的离散 TIER_BONUS。
WEIGHT_BONUS_SCALE = 0.5

# ---- 综合层检索排序（wiki 行是"附加缓存"，不喧宾夺主，符合 §0"provenance 居中"）----
# blend 排序对 wiki 行降权：新鲜页仅象征性让位于原始文献（同分优先文献），
# stale 页（被新论文影响、待重生）显著降权。单位与 TIER_BONUS 同量纲（reranker 分 + bonus）。
WIKI_BASE_PENALTY  = 0.05
WIKI_STALE_PENALTY = 0.5
