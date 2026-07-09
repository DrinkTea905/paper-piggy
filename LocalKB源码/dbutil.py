# -*- coding: utf-8 -*-
"""LanceDB 谓词工具。

解决一个贯穿全库的归一化陷阱：LanceDB 表内 `key` 列存的是**原始文献 key**
（含 <>、|、: 等 Windows 非法字符），而进度文件/批次里用的是 `safe_name(key)`
（非法字符已被替成 `_`）。用 stem 直接拼 `key = '<stem>'` 谓词会匹配 0 行，
导致删除失效、重复入库。本模块统一：由 stem 反查真实原始 key，并安全构造谓词
（转义单引号，防原始 key 含 `'` 破坏 SQL）。

供 03_embed_index / m2_finish / ingest_digest 复用。
"""
import json
import config as C


def sql_quote(s) -> str:
    """转义单引号，供 LanceDB SQL 字符串字面量安全拼接。"""
    return str(s).replace("'", "''")


def key_predicate(keys):
    """由**原始文献 key** 列表构造 `key = '...' OR ...` 谓词（已转义单引号）。

    传入空列表 / 全为假值时返回 None（调用方应据此跳过 delete，避免误删全表）。
    """
    keys = [k for k in keys if k]
    if not keys:
        return None
    return " OR ".join(f"key = '{sql_quote(k)}'" for k in keys)


def original_key_for_stem(stem: str):
    """由 safe_name(stem) 反查表内真实原始文献 key。

    读 data/chunks/<stem>.json（首块的 'key'）或 data/extracted/<stem>.json 的 'key' 字段。
    找不到返回 None（调用方应视为异常，勿静默继续删除）。
    """
    p = C.CHUNKS / f"{stem}.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                k = data[0].get("key")
                if k:
                    return k
        except Exception:
            pass
    p = C.EXTRACTED / f"{stem}.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("key"):
                return data.get("key")
        except Exception:
            pass
    return None
