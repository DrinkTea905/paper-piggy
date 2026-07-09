# -*- coding: utf-8 -*-
"""
文件夹模式数据源：扫受管文件夹里的 PDF，读 meta_cache 组装记录 dict（形状=zotero_source）。
不调用 LLM——只读 meta_cache（LLM 抽题录在 folder_ingest 里先跑）。cache 为空时全部退化为
文件名 title + needs_review，词法索引仍可秒建（元数据粗糙）。
"""
import sys, os, json, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C


def scan(folder):
    """递归找 PDF，返回绝对路径列表（排序稳定）。"""
    try:
        return sorted(str(p) for p in Path(folder).rglob("*.pdf") if p.is_file())
    except Exception:
        return []


def stable_key(folder, pdf_path):
    """稳定 key = 'f_' + sha1(相对路径)[:10]（重命名才变）。"""
    try:
        rel = os.path.relpath(pdf_path, folder).replace("\\", "/")
    except Exception:
        rel = str(pdf_path)
    return "f_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]


def _load_cache():
    f = C.FOLDER_META_CACHE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _subfolder_cats(folder, pdf):
    """子文件夹 → 分类（可选增强）。一期返回 []（契约里 collections 空）。"""
    return []


def load_papers(folder):
    """扫描 + 读 meta_cache，组装记录 dict（14 字段 + needs_review）。"""
    cache = _load_cache()
    out = []
    for pdf in scan(folder):
        key = stable_key(folder, pdf)
        entry = cache.get(key) or {}
        m = entry.get("meta") or {}
        title = m.get("title") or Path(pdf).stem
        out.append({
            "key": key, "title": title,
            "author": m.get("author", ""), "year": m.get("year", ""),
            "journal": m.get("journal", ""), "doi": m.get("doi", ""),
            "langid": m.get("langid", ""), "keywords": m.get("keywords", ""),
            "abstract": m.get("abstract", ""), "itemtype": m.get("itemtype", "journalArticle"),
            "official_pages": m.get("official_pages", ""),
            "has_pdf": True, "pdf_path": pdf,
            "collections": _subfolder_cats(folder, pdf),
            "needs_review": bool(entry.get("needs_review", True)),
        })
    return out
