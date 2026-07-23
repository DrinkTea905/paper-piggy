# -*- coding: utf-8 -*-
"""
文件夹模式数据源：扫受管文件夹里的可读全文文件，读 meta_cache 组装记录 dict（形状=zotero_source）。
不调用 LLM——只读 meta_cache（LLM 抽题录在 folder_ingest 里先跑）。cache 为空时全部退化为
文件名 title + needs_review，词法索引仍可秒建（元数据粗糙）。
"""
import sys, os, json, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import document_formats as DF

EXTRA_META_FIELDS = (
    "url", "website_title", "access_date", "publisher", "place", "isbn", "edition", "series",
    "book_title", "university", "thesis_type", "institution", "report_type", "report_number",
    "conference_name", "proceedings_title", "court", "docket_number", "decision_date",
    "standard_number", "version",
)


def scan(folder):
    """递归找 PDF/EPUB/DOCX/Markdown/TXT，返回绝对路径列表（排序稳定）。
       排除 0_Agent* 目录与符号链接/目录联接，并要求解析后的真实路径仍在受管目录内。
       这样文件夹模式不会沿重解析点读到用户未选择的外部文件并把内容送去索引/API。"""
    try:
        base = Path(folder).resolve(strict=True)
        if not base.is_dir():
            return []
        out = []
        for root, dirs, files in os.walk(base, topdown=True, followlinks=False):
            root_path = Path(root)
            kept_dirs = []
            for name in sorted(dirs):
                p = root_path / name
                if name.startswith("0_Agent") or _is_link_or_junction(p):
                    continue
                try:
                    p.resolve(strict=True).relative_to(base)
                except (OSError, ValueError):
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs                 # 原地剪枝：绝不进入链接/联接或越界目录

            for name in sorted(files):
                p = root_path / name
                if p.suffix.lower() not in DF.SUPPORTED_EXTENSIONS or _is_link_or_junction(p):
                    continue
                try:
                    real = p.resolve(strict=True)
                    real.relative_to(base)
                except (OSError, ValueError):
                    continue
                if real.is_file():
                    out.append(str(real))
        return sorted(out)
    except Exception:
        return []


def _is_link_or_junction(path):
    """Python 3.12 能识别 Windows junction；其它平台/旧解释器安全退化。"""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def stable_key(folder, file_path):
    """稳定 key = 'f_' + sha1(相对路径)[:10]（重命名才变）。"""
    try:
        rel = os.path.relpath(Path(file_path).resolve(), Path(folder).resolve()).replace("\\", "/")
    except Exception:
        rel = str(file_path)
    return "f_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]


def _load_cache():
    f = C.FOLDER_META_CACHE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _subfolder_cats(folder, source_path):
    """子文件夹 → 分类（可选增强）。一期返回 []（契约里 collections 空）。"""
    return []


def load_papers(folder):
    """扫描 + 读 meta_cache，组装记录 dict（14 字段 + needs_review）。"""
    cache = _load_cache()
    out = []
    for source_path in scan(folder):
        key = stable_key(folder, source_path)
        entry = cache.get(key) or {}
        m = entry.get("meta") or {}
        title = m.get("title") or Path(source_path).stem
        fmt = DF.detect_format(source_path)
        paper = {
            "key": key, "title": title,
            "author": m.get("author", ""), "year": m.get("year", ""),
            "journal": m.get("journal", ""), "doi": m.get("doi", ""),
            "langid": m.get("langid", ""), "keywords": m.get("keywords", ""),
            "abstract": m.get("abstract", ""), "itemtype": m.get("itemtype", "journalArticle"),
            "official_pages": m.get("official_pages", ""),
            "collections": _subfolder_cats(folder, source_path),
            "needs_review": bool(entry.get("needs_review", True)),
        }
        DF.apply_attachment_fields(paper, [{"format": fmt, "path": source_path}])
        paper.update({field: m.get(field, "") for field in EXTRA_META_FIELDS})
        out.append(paper)
    return out
