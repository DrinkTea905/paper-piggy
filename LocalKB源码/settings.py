# -*- coding: utf-8 -*-
"""
用户设置（data/settings.json）——检索引擎后端的单一事实来源。
backend = "local"（默认，离线用本地 ONNX 模型）| "api"（用 OpenAI 兼容的嵌入/重排 API，省 1.2GB）。
API 模式默认接 SiliconFlow（BAAI/bge-m3 + bge-reranker-v2-m3 免费）。
⚠️ 铁律：建索引与查询必须用同一后端（本地 INT8 与 API 全精度向量不一致，混用掉点）。
   建库时把 backend 写进 index_manifest；加载/查询时若不一致要提示重建。
"""
import sys, json, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

_LOCK = threading.RLock()   # 可重入：save() 持锁时会调 load()（也要持锁），普通 Lock 会自死锁
_CACHE = {"data": None, "mtime": -1}

DEFAULT = {
    "backend": "local",
    # 数据源：zotero（读 zotero.sqlite）| folder（受管文件夹 + LLM 抽题录）。缺省 zotero，老用户零影响。
    "source": "zotero",
    "folder_dir": "",            # source=folder 时的受管库文件夹绝对路径
    # BF2：用户在向导手选的 Zotero 数据目录（空=自动探测）。此前校验完即丢，重启就失忆。
    "zotero_dir": "",
    "import_only_pdf": False,    # Zotero 模式：只导入有 PDF 的条目（向导可选，切换需重建即时索引）
    # 整库锁定单学科（journal_grading 期刊权重引擎用）。产品默认标准 "law"；
    # 个人法学库改 "law_personal"（含 2026-06-28 旧档：台湾刊/顶尖外文法评/外文权威，外刊不打折）。
    "journal_discipline": "law",
    # 自动更新：Zotero 新增条目 / 文件夹新增 PDF 时，后台定时增量更新（只跑轻量层+语义，深索永远手动）。
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
    # 自动 SAC（M2）：深索时用 LLM 给每篇生成 ~150 字摘要当嵌入前缀，提升检索。
    # 默认用 SiliconFlow 免费的 Qwen2.5-7B-Instruct（纯指令模型、无思维链、出词快，最适合做短摘要）；
    # key 空或 enabled=False 则退化为纯文本嵌入。
    "sac": {
        "enabled": False,
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


def discipline():
    """整库锁定的期刊分级学科（journal_grading）。默认标准 law。"""
    return load().get("journal_discipline", "law")


def source():
    """数据源：zotero | folder。缺省 zotero。"""
    return load().get("source", "zotero")


def folder_dir():
    return load().get("folder_dir", "")


def folder_meta_conf():
    return load().get("folder_meta", DEFAULT["folder_meta"])


def api_conf():
    return load().get("api", DEFAULT["api"])


def sac_conf():
    """SAC 配置。K2：补 generator 字段（server|agent|off）——
       老配置无该字段时据 enabled 迁移：enabled=True→server（服务端自动生成）、False→off（不生成）。
       不写进 DEFAULT，避免 _merge 用默认值盖掉老用户的 enabled 语义。"""
    c = dict(load().get("sac", DEFAULT["sac"]))
    g = c.get("generator")
    if g not in ("server", "agent", "off"):
        c["generator"] = "server" if c.get("enabled") else "off"
    return c


def is_api():
    return backend() == "api"
