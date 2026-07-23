# -*- coding: utf-8 -*-
"""
用户设置（data/settings.json）——检索引擎后端的单一事实来源。
backend = "local"（默认，离线用本地 ONNX 模型）| "api"（用 OpenAI 兼容的嵌入/重排 API，省 1.2GB）。
API 模式默认接 SiliconFlow（BAAI/bge-m3 + bge-reranker-v2-m3 免费）。
⚠️ 铁律：建索引与查询必须用同一向量空间（后端、API 地址、嵌入模型任一变化都可能不兼容）。
   建库时把 backend + embedding_identity 写进 index_manifest；加载/查询不一致就停止并提示重建。
"""
import sys, json, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

_LOCK = threading.RLock()   # 可重入：save() 持锁时会调 load()（也要持锁），普通 Lock 会自死锁
_CACHE = {"data": None, "mtime": -1}

DEFAULT = {
    "backend": "local",
    # 检索组件按需驻留：首次检索才载入本地 ONNX / BM25；连续空闲后释放。
    # 0 = 始终保留（下一次检索最快，但会持续占用更多内存）。
    "retrieval": {
        "idle_unload_min": 10,
    },
    # 数据源：zotero（读 zotero.sqlite）| folder（受管文件夹 + LLM 抽题录）。缺省 zotero，老用户零影响。
    "source": "zotero",
    "folder_dir": "",            # source=folder 时的受管库文件夹绝对路径
    # BF2：用户在向导手选的 Zotero 数据目录（空=自动探测）。此前校验完即丢，重启就失忆。
    "zotero_dir": "",
    # Zotero 模式：只导入有受支持全文附件的条目。为兼容已有用户保留旧键名 import_only_pdf。
    # 支持 PDF、EPUB、DOCX、Markdown、TXT；不支持 HTML。默认 True。
    # ⚠️ 代价：没有这些附件的法条/法规/网页法源会被挡在库外——
    #    这是产品所有者 2026-07-15 明确拍板的默认；想收全法源就在设置里关掉它。
    "import_only_pdf": True,
    # 整库锁定单学科（journal_grading 期刊权重引擎用）。默认 "law_personal"
    #（法学·开发者增强，含 2026-06-28 旧档：台湾刊/顶尖外文法评/外文权威，外刊不打折）；
    # 标准法学是 "law"。两者的 catalog 都随包分发，law_personal 已验证可正常加载。
    "journal_discipline": "law_personal",
    # 自动更新：Zotero 新增条目 / 文件夹新增全文文件时，后台定时增量更新（只跑轻量层+语义，深索永远手动）。
    # 调度由「分钟级轮询」改为「按天(1-30) + 指定时刻」，默认每天 07:00——时段长、避开用库时间、降低与检索撞车。
    "auto_update": {
        "enabled": True,
        # 遗留字段：仅供老配置迁移参考，新调度不再用它（保留以免 _merge 丢字段）。
        "interval_min": 60,
        # 按天间隔(1-30)：默认每天。UI 唯一事实来源——server/app.js 兜底一律引用此值。
        "interval_days": 1,
        # 每次触发的时刻 HH:MM（24h）。默认早 7 点。
        "at_time": "07:00",
        # 开应用时若上次计划更新已被错过（关机期间到点），补跑一次。
        "catch_up_on_launch": True,
        # C3/D4-4：同步删除（默认关）。开=移出受管文件夹的全文文件下次更新时从库中移除；
        # 关=只增不删（防误删临时挪走的文件）。仅 folder 模式的增量构建读它；zotero 走手动「清理已删除」按钮。
        "delete_sync": False,
    },
    # 文件夹模式的题录抽取 LLM（key 空时复用 api/sac 的 key，见 folder_meta._conf）
    "folder_meta": {
        "enabled": True,
        "base": "https://api.siliconflow.cn/v1",
        "key": "",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "workers": 3,
    },
    "api": {
        "base": "https://api.siliconflow.cn/v1",
        "key": "",
        "embed_model": "BAAI/bge-m3",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
    },
    # 应用内更新（updater.py）。mirror_base：国内镜像的目录前缀，用来兜底 GitHub 下载超时。
    # 只需在该前缀下放两个小文件：paper-piggy-app-<版本>.zip 和同名 .sha256（约 1MB）。
    # 例：填 "https://gitee.com/你的名/paper-piggy/releases/download/v1.0.4/" 或 R2 桶的公开前缀。
    # 空=只走 GitHub。检查更新仍走 GitHub API（那个小、通常能连上），镜像只兜底大文件下载。
    "update": {
        "mirror_base": "",
    },
    # 备份与恢复（backup.py）。把「丢了就再也没有的东西」打成 zip，放哪由用户定。
    # dir 可以指到 OneDrive 里的某个文件夹 —— 同步一个**静态 zip** 是安全的，
    # 而让云盘去实时同步 lancedb 那种持续读写的数据库，早晚会把索引搞坏（见 backup.py 文件头）。
    "backup": {
        "dir": "",                # 空 = data/backups；用户可改到任意目录（含云盘同步目录）
        "auto": False,            # 自动备份（只打「手写资产」，不含索引 —— 否则每次几个 G）
        "every_days": 7,          # 自动备份间隔（天）
        "keep": 3,                # 只保留最近 N 份，免得把云盘撑爆
        "include_index": False,   # 手动备份时是否连向量索引一起打包（几个 G，换机免重建）
        "last_at": "",            # 上次成功备份的时间（YYYY-MM-DD HH:MM:SS）
    },
    # 自动 SAC（M2）：深索后用 LLM 给每篇生成 ~150 字摘要当嵌入前缀，提升检索。
    # source=reuse：复用检索引擎的 SiliconFlow Key + 当前免费、简单快速的 Qwen2.5-7B-Instruct；
    # source=custom：使用用户另选的文本生成厂商 / Key / 模型（通常按量收费）。
    # source 空是旧配置迁移态：有独立 key 视为 custom，否则视为 reuse（见 sac_conf）。
    "sac": {
        "enabled": False,           # 遗留字段；真正的门控是 generator（见 sac_conf / sac.enabled）
        # 深索摘要由谁生成：agent=交给 Agent（服务端不自动产，默认）| server=服务端用 API Key 自动产 | off=不产。
        # 默认 agent：省 API 额度、契合「以 Agent 为主」的用法。代价——应用内自己点深索时不会自动产摘要，
        # 得让 Agent 跑深索时顺带生成，或在浏览页/库总览手动补。
        "generator": "agent",
        "source": "",
        "provider": "deepseek",
        "base": "https://api.siliconflow.cn/v1",
        "key": "",
        "model": "Qwen/Qwen2.5-7B-Instruct",
    },
}

SETTINGS_FILE = C.DATA / "settings.json"


def _write_json_atomic(path, obj):
    """原子写，对 OneDrive/杀软临时占用导致的 os.replace WinError 5 重试 + 兜底直写。"""
    import os, time
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    for i in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.15 * (i + 1))
    path.write_text(data, encoding="utf-8")
    try:
        if tmp.exists():
            tmp.unlink()
    except Exception:
        pass


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    """读设置（带默认合并 + mtime 缓存）。文件损坏时退回默认，不崩。"""
    with _LOCK:
        try:
            if SETTINGS_FILE.exists():
                mt = SETTINGS_FILE.stat().st_mtime
                if _CACHE["data"] is None or _CACHE["mtime"] != mt:
                    raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                    _CACHE["data"] = _merge(DEFAULT, raw); _CACHE["mtime"] = mt
                return dict(_CACHE["data"])
        except Exception as e:
            print(f"[settings] 读取失败，用默认（{e}）", file=sys.stderr, flush=True)
        return _merge(DEFAULT, {})


def save(patch):
    """合并保存（原子写）。返回合并后的完整设置。"""
    with _LOCK:
        cur = load()
        merged = _merge(cur, patch)
        _write_json_atomic(SETTINGS_FILE, merged)
        _CACHE["data"] = merged
        _CACHE["mtime"] = SETTINGS_FILE.stat().st_mtime
        return merged


def reset():
    """恢复默认：清 API/SAC/folder_meta key、学科回 law、检索后端回 local。
       **保留数据源设置**（source/folder_dir/zotero_dir）与自动更新开关——否则文件夹模式用户点「恢复默认」后
       source 被打回 zotero，自动更新会按 zotero 源全量重写 papers.jsonl，原文件夹库(f_ 前缀 key)及挂在其上的
       深索/分类派生数据整体失联（前端确认文案也承诺『文献索引不受影响』，必须名副其实）。
       不动浏览器里的 LLM 对话 key（那存 localStorage，由前端单独清）。"""
    with _LOCK:
        cur = load()
        keep = {
            "source": cur.get("source", DEFAULT["source"]),
            "folder_dir": cur.get("folder_dir", ""),
            "zotero_dir": cur.get("zotero_dir", ""),
            "import_only_pdf": cur.get("import_only_pdf", DEFAULT["import_only_pdf"]),
            "auto_update": cur.get("auto_update", DEFAULT["auto_update"]),
        }
        merged = _merge(DEFAULT, keep)
        _write_json_atomic(SETTINGS_FILE, merged)
        _CACHE["data"] = dict(merged)
        _CACHE["mtime"] = SETTINGS_FILE.stat().st_mtime
        return dict(merged)


def backend():
    return load().get("backend", "local")


def embedding_identity(state=None):
    """向量空间身份：后端、API 地址和嵌入模型任一变化都必须全量重嵌。"""
    state = state or load()
    kind = state.get("backend", "local")
    if kind == "api":
        api = state.get("api") or {}
        base = str(api.get("base") or DEFAULT["api"]["base"]).strip().rstrip("/").lower()
        model = str(api.get("embed_model") or DEFAULT["api"]["embed_model"]).strip()
        return f"api|{base}|{model}"
    return f"local|{C.EMBED_MODEL}|dim={C.EMBED_DIM}"


def discipline():
    """整库锁定的期刊分级学科（journal_grading）。默认见 DEFAULT（law_personal）。"""
    return load().get("journal_discipline", DEFAULT["journal_discipline"])


def source():
    """数据源：zotero | folder。缺省 zotero。"""
    return load().get("source", "zotero")


def folder_dir():
    return load().get("folder_dir", "")


def folder_meta_conf():
    return load().get("folder_meta", DEFAULT["folder_meta"])


def api_conf():
    return load().get("api", DEFAULT["api"])


def retrieval_conf():
    """检索组件内存策略；idle_unload_min=0 表示不自动释放。"""
    c = dict(load().get("retrieval", DEFAULT["retrieval"]))
    try:
        mins = int(c.get("idle_unload_min", DEFAULT["retrieval"]["idle_unload_min"]))
    except (TypeError, ValueError):
        mins = DEFAULT["retrieval"]["idle_unload_min"]
    c["idle_unload_min"] = 0 if mins == 0 else min(1440, max(1, mins))
    return c


def sac_conf():
    """SAC 配置，兼容旧版没有 ``source`` 的独立摘要 Key。

    generator 决定由谁生成；source 只在 generator=server 时决定自动生成所用凭据：
    reuse=复用检索引擎的 SiliconFlow Key，custom=使用另配的文本生成厂商。
    """
    c = dict(DEFAULT["sac"])
    c.update(load().get("sac") or {})
    g = c.get("generator")
    if g not in ("server", "agent", "off"):
        c["generator"] = "server" if c.get("enabled") else "off"
    if c.get("source") not in ("reuse", "custom"):
        c["source"] = "custom" if c.get("key") else "reuse"
    if not c.get("provider"):
        c["provider"] = "deepseek"
    return c


def is_api():
    return backend() == "api"
