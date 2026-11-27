# -*- coding: utf-8 -*-
"""
轻量无界面「结构化研究矩阵表」（Milestone 3）：
- 面向法学实证样本编码、多国少年司法制度比较矩阵、核心文献观点对比等场景：
  Agent 可逐行 upsert / 改单个单元格，不再每次把整张长表重吐一遍。
- 安全边界：仅允许落在 `0_Agent交付物/<课题>/<表名>.table.json` 或
  `0_Agent资料库/研究表格/<表名>.table.json`，拒绝路径穿越、符号链接与目录联接。
- 保障：列类型校验（text/select/number/date/boolean/paper_key/printed_page）、
  `revision` 乐观并发检查、`request_id` 幂等去重、原子写盘；
  支持导出 UTF-8-BOM CSV（Excel/SPSS/Stata 直接打开）与 Word 标准三线表 `.docx`。
"""
import csv
import datetime
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as C

SUPPORTED_COL_TYPES = ("text", "select", "number", "date", "boolean", "paper_key", "printed_page")
TABLE_FORMAT = "paperpiggy-table-v1"
_MAX_REQUEST_IDS = 100


class TableError(ValueError):
    pass


class RevisionConflictError(TableError):
    pass


def _is_link_or_junction(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        ij = getattr(os.path, "isjunction", None)
        return bool(ij and ij(p))
    except OSError:
        return True


def _allowed_roots():
    import agent_ws as AW
    AW.ensure_scaffold()
    out_dir = Path(AW.output_dir()).resolve()
    tab_dir = (Path(AW.rely_dir()).resolve() / "研究表格")
    tab_dir.mkdir(parents=True, exist_ok=True)
    return out_dir, tab_dir


def resolve_table_path(path, allow_non_table_suffix=False):
    """把相对或绝对路径解析为受限根目录下的安全 Path。"""
    raw = str(path or "").strip()
    if not raw or "\x00" in raw:
        raise TableError("需要有效表格路径（如「少年司法/样本编码.table.json」或「研究表格/对比矩阵.table.json」）。")
    out_dir, tab_dir = _allowed_roots()
    norm_raw = raw.replace("\\", "/")
    if ".." in norm_raw.split("/"):
        raise TableError("路径不允许包含「..」。")

    cand = Path(raw)
    if cand.is_absolute():
        target = cand
    elif norm_raw.startswith(("0_Agent交付物/", "0_Agent资料库/研究表格/")):
        target = (out_dir.parent / cand)
    elif norm_raw == "研究表格" or norm_raw.startswith("研究表格/"):
        rel = norm_raw[len("研究表格"):].lstrip("/")
        target = tab_dir / rel
    else:
        target = out_dir / cand

    if not allow_non_table_suffix and not target.name.endswith(".table.json"):
        if target.suffix.lower() == ".json":
            target = target.with_name(target.stem + ".table.json")
        else:
            target = target.with_name(target.name + ".table.json")

    resolved = target.resolve(strict=False)
    allowed = False
    for root in (out_dir, tab_dir):
        try:
            resolved.relative_to(root)
            if resolved != root:
                allowed = True
                break
        except ValueError:
            continue
    if not allowed:
        raise TableError("表格文件只允许存放在「0_Agent交付物」或「0_Agent资料库/研究表格」下。")

    for parent in [resolved] + list(resolved.parents):
        if parent == out_dir or parent == tab_dir or parent == out_dir.parent:
            break
        if parent.exists() and _is_link_or_junction(parent):
            raise TableError("路径包含符号链接或目录联接，已拒绝。")
    return resolved


def _atomic_write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    last = None
    for i in range(6):
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.05 * (i + 1))
    raise last


def _load_paper_map():
    out = {}
    try:
        import document_formats as DF
        if C.PAPERS_JSONL.exists():
            with open(C.PAPERS_JSONL, encoding="utf-8") as f:
                for ln in f:
                    if ln.strip():
                        p = DF.normalize_record(json.loads(ln))
                        if p.get("key"):
                            out[p["key"]] = p
    except Exception:
        pass
    return out


def _normalize_columns(columns):
    if not isinstance(columns, list) or not columns:
        raise TableError("columns 必须是非空列定义列表。")
    norm, seen = [], set()
    for col in columns:
        if not isinstance(col, dict):
            raise TableError("每列定义必须是对象。")
        cid = str(col.get("id") or col.get("name") or "").strip()
        name = str(col.get("name") or cid).strip()
        ctype = str(col.get("type") or "text").strip().lower()
        if not cid:
            raise TableError("列定义缺少 id / name。")
        if cid in seen:
            raise TableError(f"列 id 重复：{cid}")
        if ctype not in SUPPORTED_COL_TYPES:
            raise TableError(f"列「{cid}」不支持类型 {ctype}（可选：{' / '.join(SUPPORTED_COL_TYPES)}）。")
        seen.add(cid)
        item = {"id": cid, "name": name, "type": ctype}
        if ctype == "select":
            opts = [str(x).strip() for x in (col.get("options") or []) if str(x).strip()]
            item["options"] = opts
        if col.get("unit"):
            item["unit"] = str(col["unit"]).strip()
        norm.append(item)
    return norm


def _validate_cell(col, val, paper_map=None, allow_external_keys=False):
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    ctype = col["type"]
    cid = col["id"]
    if ctype == "text":
        return str(val).strip()
    if ctype == "select":
        s = str(val).strip()
        opts = col.get("options") or []
        if opts and s not in opts:
            raise TableError(f"列「{cid}」取值「{s}」不在允许选项中：{opts}")
        return s
    if ctype == "number":
        if isinstance(val, bool):
            raise TableError(f"列「{cid}」需要 number，不能传布尔值。")
        try:
            num = float(val) if isinstance(val, (int, float, str)) else None
        except (TypeError, ValueError):
            num = None
        if num is None or math.isnan(num) or math.isinf(num):
            raise TableError(f"列「{cid}」需要合法数字，收到：{val!r}")
        return int(num) if (isinstance(val, int) or num.is_integer()) else num
    if ctype == "date":
        s = str(val).strip()
        try:
            datetime.datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            raise TableError(f"列「{cid}」需要 YYYY-MM-DD 日期，收到：{s!r}")
        return s
    if ctype == "boolean":
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in ("true", "1", "yes", "是", "适用"):
            return True
        if s in ("false", "0", "no", "否", "不适用"):
            return False
        raise TableError(f"列「{cid}」需要 boolean（true/false），收到：{val!r}")
    if ctype == "paper_key":
        k = str(val).strip()
        if not allow_external_keys and paper_map is not None and k not in paper_map:
            raise TableError(f"列「{cid}」文献 key「{k}」不在当前知识库 papers.jsonl 中；若确实为库外临时编号可传 allow_external_keys=true。")
        return k
    if ctype == "printed_page":
        s = str(val).strip()
        if len(s) > 32 or not re.fullmatch(r"[A-Za-z0-9IVXLCDMivxlcdm页第\-\–—~,，、\s]+", s):
            raise TableError(f"列「{cid}」印刷页码格式不规范：{s!r}")
        return s
    return val


def _load_table(resolved: Path):
    if not resolved.exists():
        raise TableError(f"研究表格不存在：{resolved}")
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != TABLE_FORMAT:
        raise TableError(f"文件不是有效的 PaperPiggy 研究表格：{resolved}")
    return data


def _replay_hit(doc, request_id):
    rid = str(request_id or "").strip()
    if not rid:
        return None
    for entry in (doc.get("recent_requests") or []):
        if isinstance(entry, dict) and entry.get("request_id") == rid:
            res = dict(entry.get("summary") or {})
            res["idempotent_replay"] = True
            return res
    return None


def _record_request(doc, request_id, summary):
    rid = str(request_id or "").strip()
    if not rid:
        return
    hist = list(doc.get("recent_requests") or [])
    hist.append({"request_id": rid, "summary": summary})
    doc["recent_requests"] = hist[-_MAX_REQUEST_IDS:]


def list_tables():
    out_dir, tab_dir = _allowed_roots()
    items = []
    for root, group in ((out_dir, "交付物"), (tab_dir, "资料库/研究表格")):
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.table.json")):
            if _is_link_or_junction(p):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                items.append({
                    "path": str(p),
                    "rel_path": p.relative_to(root).as_posix(),
                    "location": group,
                    "title": d.get("title") or p.stem,
                    "revision": int(d.get("revision") or 1),
                    "n_columns": len(d.get("columns") or []),
                    "n_rows": len(d.get("rows") or []),
                    "updated_at": d.get("updated_at") or "",
                })
            except Exception:
                continue
    return items


def create_or_evolve_table(path, title="", columns=None, description="",
                           if_revision=None, request_id=""):
    """新建或演进一张结构化研究矩阵表。"""
    resolved = resolve_table_path(path)
    cols = _normalize_columns(columns or [])
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if resolved.exists():
        doc = _load_table(resolved)
        replay = _replay_hit(doc, request_id)
        if replay is not None:
            return replay
        if if_revision is not None and int(if_revision) != int(doc.get("revision", 1)):
            raise RevisionConflictError(
                f"表格版本冲突：期望 revision={if_revision}，当前为 {doc.get('revision')}。"
            )
        old_cols = {c["id"]: c for c in (doc.get("columns") or [])}
        new_ids = {c["id"] for c in cols}
        removed = [cid for cid in old_cols if cid not in new_ids]
        non_empty_removed = [
            cid for cid in removed
            if any((r.get("cells") or {}).get(cid) is not None for r in (doc.get("rows") or []))
        ]
        if non_empty_removed:
            raise TableError(f"不能删除仍含数据的列：{'、'.join(non_empty_removed)}")
        doc["columns"] = cols
        if title:
            doc["title"] = str(title).strip()
        if description is not None:
            doc["description"] = str(description).strip()
        doc["revision"] = int(doc.get("revision") or 1) + 1
        doc["updated_at"] = now
        summary = {
            "ok": True, "created": False, "path": str(resolved),
            "title": doc["title"], "revision": doc["revision"],
            "n_columns": len(cols), "n_rows": len(doc.get("rows") or []),
        }
        _record_request(doc, request_id, summary)
        _atomic_write_json(resolved, doc)
        return summary

    doc = {
        "format": TABLE_FORMAT,
        "schema_rev": 1,
        "title": str(title or resolved.name.replace(".table.json", "")).strip(),
        "description": str(description or "").strip(),
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "columns": cols,
        "rows": [],
        "recent_requests": [],
    }
    summary = {
        "ok": True, "created": True, "path": str(resolved),
        "title": doc["title"], "revision": 1,
        "n_columns": len(cols), "n_rows": 0,
    }
    _record_request(doc, request_id, summary)
    _atomic_write_json(resolved, doc)
    return summary


def _row_matches(row, filters):
    if not filters:
        return True
    cells = row.get("cells") or {}
    for flt in filters:
        if not isinstance(flt, dict):
            continue
        col = flt.get("column")
        if not col:
            continue
        val = cells.get(col)
        if flt.get("is_empty") is True:
            if val is not None and str(val).strip() != "":
                return False
        if flt.get("is_empty") is False:
            if val is None or str(val).strip() == "":
                return False
        if "equals" in flt:
            want = flt["equals"]
            if want is None:
                if val is not None and str(val).strip() != "":
                    return False
            elif val != want:
                return False
        if "contains" in flt and flt["contains"] is not None:
            needle = str(flt["contains"]).casefold()
            if needle not in str(val or "").casefold():
                return False
        if "in" in flt and isinstance(flt["in"], list):
            if val not in flt["in"]:
                return False
        if "gt" in flt and flt["gt"] is not None:
            if val is None or not isinstance(val, (int, float)) or val <= flt["gt"]:
                return False
        if "lt" in flt and flt["lt"] is not None:
            if val is None or not isinstance(val, (int, float)) or val >= flt["lt"]:
                return False
    return True


def query_table(path=None, filters=None, sort_by=None, desc=False, offset=0, limit=100):
    """不传 path 时列出全部研究表格；传 path 时返回 schema + revision + 筛选后的行。"""
    if not str(path or "").strip():
        tables = list_tables()
        return {"ok": True, "tables": tables, "total": len(tables)}
    resolved = resolve_table_path(path)
    doc = _load_table(resolved)
    rows = [r for r in (doc.get("rows") or []) if _row_matches(r, filters)]
    if sort_by:
        rows.sort(
            key=lambda r: (
                (r.get("cells") or {}).get(sort_by) is None,
                str((r.get("cells") or {}).get(sort_by) or ""),
            ),
            reverse=bool(desc),
        )
    off = max(0, int(offset or 0))
    lim = max(1, min(500, int(limit or 100)))
    return {
        "ok": True,
        "path": str(resolved),
        "title": doc.get("title", ""),
        "description": doc.get("description", ""),
        "revision": int(doc.get("revision") or 1),
        "columns": doc.get("columns") or [],
        "total_rows": len(doc.get("rows") or []),
        "filtered_rows": len(rows),
        "offset": off,
        "rows": rows[off:off + lim],
    }


def upsert_rows(path, rows, key_column=None, if_revision=None,
                actor="agent", request_id="", allow_external_keys=False):
    """批量新增或按行主键更新（只改涉及行，不重写全表结构）。"""
    resolved = resolve_table_path(path)
    doc = _load_table(resolved)
    replay = _replay_hit(doc, request_id)
    if replay is not None:
        return replay
    if if_revision is not None and int(if_revision) != int(doc.get("revision", 1)):
        raise RevisionConflictError(
            f"表格版本冲突：期望 revision={if_revision}，当前为 {doc.get('revision')}。"
        )
    col_map = {c["id"]: c for c in (doc.get("columns") or [])}
    paper_map = _load_paper_map() if any(c["type"] == "paper_key" for c in col_map.values()) else None
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    existing = list(doc.get("rows") or [])
    by_rid = {r.get("row_id"): i for i, r in enumerate(existing) if r.get("row_id")}
    by_key = {}
    if key_column:
        if key_column not in col_map:
            raise TableError(f"key_column「{key_column}」不在表格列定义中。")
        for i, r in enumerate(existing):
            kv = (r.get("cells") or {}).get(key_column)
            if kv is not None and str(kv).strip():
                by_key[str(kv).strip()] = i

    inserted, updated, row_ids = 0, 0, []
    for raw_row in (rows or []):
        if not isinstance(raw_row, dict):
            raise TableError("每行数据必须是对象。")
        rid = str(raw_row.get("row_id") or "").strip()
        cells_in = raw_row.get("cells") if isinstance(raw_row.get("cells"), dict) else raw_row
        cleaned = {}
        for k, v in cells_in.items():
            if k in ("row_id", "updated_at", "updated_by"):
                continue
            if k not in col_map:
                raise TableError(f"未知列「{k}」；请先在 table_create 中定义。")
            cleaned[k] = _validate_cell(col_map[k], v, paper_map=paper_map,
                                        allow_external_keys=allow_external_keys)

        target_idx = by_rid.get(rid) if rid else None
        if target_idx is None and key_column and cleaned.get(key_column) is not None:
            target_idx = by_key.get(str(cleaned[key_column]).strip())

        if target_idx is not None:
            row_obj = existing[target_idx]
            row_obj.setdefault("cells", {}).update(cleaned)
            row_obj["updated_at"] = now
            row_obj["updated_by"] = str(actor or "agent")
            updated += 1
            row_ids.append(row_obj["row_id"])
        else:
            new_id = rid or ("r_" + uuid.uuid4().hex[:8])
            row_cells = {cid: None for cid in col_map}
            row_cells.update(cleaned)
            row_obj = {
                "row_id": new_id,
                "cells": row_cells,
                "updated_at": now,
                "updated_by": str(actor or "agent"),
            }
            existing.append(row_obj)
            idx = len(existing) - 1
            by_rid[new_id] = idx
            if key_column and row_cells.get(key_column) is not None:
                by_key[str(row_cells[key_column]).strip()] = idx
            inserted += 1
            row_ids.append(new_id)

    doc["rows"] = existing
    doc["revision"] = int(doc.get("revision") or 1) + 1
    doc["updated_at"] = now
    summary = {
        "ok": True, "path": str(resolved), "revision": doc["revision"],
        "inserted": inserted, "updated": updated, "total_rows": len(existing),
        "row_ids": row_ids,
    }
    _record_request(doc, request_id, summary)
    _atomic_write_json(resolved, doc)
    return summary


def patch_cells(path, patches, if_revision=None, actor="agent",
                request_id="", allow_external_keys=False):
    """按 row_id 精确修补若干单元格（如补全空白编码项）。"""
    resolved = resolve_table_path(path)
    doc = _load_table(resolved)
    replay = _replay_hit(doc, request_id)
    if replay is not None:
        return replay
    if if_revision is not None and int(if_revision) != int(doc.get("revision", 1)):
        raise RevisionConflictError(
            f"表格版本冲突：期望 revision={if_revision}，当前为 {doc.get('revision')}。"
        )
    col_map = {c["id"]: c for c in (doc.get("columns") or [])}
    paper_map = _load_paper_map() if any(c["type"] == "paper_key" for c in col_map.values()) else None
    by_rid = {r.get("row_id"): r for r in (doc.get("rows") or [])}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    patched = 0
    for p in (patches or []):
        if not isinstance(p, dict):
            raise TableError("每条 patch 必须是对象。")
        rid = str(p.get("row_id") or "").strip()
        if rid not in by_rid:
            raise TableError(f"找不到 row_id={rid!r}。")
        cells = p.get("cells")
        if not isinstance(cells, dict) or not cells:
            raise TableError(f"row_id={rid} 缺少 cells 修改内容。")
        row_obj = by_rid[rid]
        for k, v in cells.items():
            if k not in col_map:
                raise TableError(f"未知列「{k}」。")
            row_obj.setdefault("cells", {})[k] = _validate_cell(
                col_map[k], v, paper_map=paper_map, allow_external_keys=allow_external_keys
            )
        row_obj["updated_at"] = now
        row_obj["updated_by"] = str(actor or "agent")
        patched += 1

    doc["revision"] = int(doc.get("revision") or 1) + 1
    doc["updated_at"] = now
    summary = {
        "ok": True, "path": str(resolved), "revision": doc["revision"],
        "patched_rows": patched, "total_rows": len(doc.get("rows") or []),
    }
    _record_request(doc, request_id, summary)
    _atomic_write_json(resolved, doc)
    return summary


def _col_header_label(col):
    name = col.get("name") or col.get("id")
    unit = col.get("unit") or ""
    return f"{name}（{unit}）" if unit else name


def _format_cell_for_human(col, val, paper_map):
    if val is None or val == "":
        return ""
    if col["type"] == "boolean":
        return "是" if val else "否"
    if col["type"] == "paper_key":
        k = str(val).strip()
        p = paper_map.get(k)
        if not p:
            return k
        try:
            import cite_format as CF
            return CF.compact(p).strip("[]")
        except Exception:
            parts = [x for x in (p.get("author"), p.get("year")) if x]
            prefix = " ".join(parts) + " " if parts else ""
            return f"{prefix}《{p.get('title', '')}》".strip()
    return str(val)


def export_table(path, fmt="csv", out_path=None):
    """导出为人类与统计软件可用的成品：csv（UTF-8-BOM）/ docx（Word 标准三线表）/ md。"""
    fmt = str(fmt or "csv").strip().lower()
    if fmt not in ("csv", "docx", "md"):
        raise TableError("导出格式仅支持 csv / docx / md。")
    resolved = resolve_table_path(path)
    doc = _load_table(resolved)
    if out_path:
        target = resolve_table_path(out_path, allow_non_table_suffix=True)
        if not target.name.lower().endswith("." + fmt):
            target = target.with_name(target.name + "." + fmt)
    else:
        stem = resolved.name[:-len(".table.json")] if resolved.name.endswith(".table.json") else resolved.stem
        target = resolved.with_name(f"{stem}.{fmt}")
    target.parent.mkdir(parents=True, exist_ok=True)

    cols = doc.get("columns") or []
    rows = doc.get("rows") or []
    paper_map = _load_paper_map() if any(c["type"] == "paper_key" for c in cols) else {}

    if fmt == "csv":
        headers = []
        for c in cols:
            headers.append(_col_header_label(c))
            if c["type"] == "paper_key":
                headers.append(f"{c.get('name') or c['id']}_人类可读引注")
        with open(target, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for r in rows:
                cells = r.get("cells") or {}
                out_row = []
                for c in cols:
                    v = cells.get(c["id"])
                    if c["type"] == "paper_key":
                        out_row.append("" if v is None else str(v))
                        out_row.append(_format_cell_for_human(c, v, paper_map))
                    elif c["type"] == "boolean":
                        out_row.append("" if v is None else ("是" if v else "否"))
                    else:
                        out_row.append("" if v is None else str(v))
                w.writerow(out_row)
    elif fmt == "md":
        headers = [_col_header_label(c) for c in cols]
        lines = [f"# {doc.get('title', '研究表格')}"]
        if doc.get("description"):
            lines.append(f"\n> {doc['description']}")
        lines.append("")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for r in rows:
            cells = r.get("cells") or {}
            vals = [_format_cell_for_human(c, cells.get(c["id"]), paper_map).replace("\n", " ").replace("|", "\\|") for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        _export_three_line_docx(doc, cols, rows, paper_map, target)

    return {
        "ok": True,
        "format": fmt,
        "path": str(target),
        "n_rows": len(rows),
        "n_columns": len(cols),
    }


def _export_three_line_docx(doc, cols, rows, paper_map, target: Path):
    """用 python-docx 生成标准中文学术三线表（顶/底 1.5pt、表头下 0.75pt，无竖线）。"""
    from docx import Document
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import nsdecls, qn
    from docx.shared import Pt

    d = Document()
    h = d.add_heading(doc.get("title") or "研究矩阵表", level=2)
    for run in h.runs:
        run.font.name = "黑体"
        run.font.size = Pt(13)
    if doc.get("description"):
        p = d.add_paragraph(doc["description"])
        for run in p.runs:
            run.font.size = Pt(10.5)

    table = d.add_table(rows=len(rows) + 1, cols=len(cols))
    table.autofit = True
    # 清除全表默认网格线
    tbl_pr = table._tbl.tblPr
    tbl_borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="single" w:sz="12" w:space="0" w:color="000000"/>'
        '<w:bottom w:val="single" w:sz="12" w:space="0" w:color="000000"/>'
        '<w:left w:val="none"/><w:right w:val="none"/>'
        '<w:insideH w:val="none"/><w:insideV w:val="none"/>'
        '</w:tblBorders>'
    )
    old_borders = tbl_pr.find(qn("w:tblBorders"))
    if old_borders is not None:
        tbl_pr.remove(old_borders)
    tbl_pr.append(tbl_borders)

    def _set_cell_bottom_rule(cell, sz=6):
        tc_pr = cell._tc.get_or_add_tcPr()
        old = tc_pr.find(qn("w:tcBorders"))
        if old is not None:
            tc_pr.remove(old)
        b = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), str(sz))
        bottom.set(qn("w:space"), "0")
        bottom.set(qn("w:color"), "000000")
        b.append(bottom)
        tc_pr.append(b)

    for j, c in enumerate(cols):
        cell = table.rows[0].cells[j]
        cell.text = _col_header_label(c)
        for p in cell.paragraphs:
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(10.5)
        _set_cell_bottom_rule(cell, sz=6)

    for i, row in enumerate(rows, start=1):
        cells = row.get("cells") or {}
        for j, c in enumerate(cols):
            cell = table.rows[i].cells[j]
            cell.text = _format_cell_for_human(c, cells.get(c["id"]), paper_map)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(10)

    d.save(str(target))
