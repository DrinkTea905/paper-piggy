# -*- coding: utf-8 -*-
"""
journal_grading —— 期刊引用权重分级引擎（独立自足，三层解耦）。
对外主 API：
    from journal_grading import resolve_journal_weight, reload
    r = resolve_journal_weight({"journal": "中国法学"}, "law")
    #  r == {"weight": 1.0, "tier": "T1", "needsReview": False, "hitCatalogs": [...], "explain": {...}}
本期(v1) 只做引擎：不改动现有 journal_tiers.py / index_light / retriever / 前端。
配置与目录数据可微调：见 config/grading_config.json 与 catalogs/*.json（或用户数据目录同名副本）。
"""
import sys as _sys
from pathlib import Path as _Path
# 本包沿用项目 flat / sys.path 风格：子模块间以顶层名互相 import（loader/resolver/…）。
# 先把包目录放上 sys.path，使 `import journal_grading` 与直接跑脚本两种方式都成立。
_sys.path.insert(0, str(_Path(__file__).parent))

from resolver import resolve_journal_weight, reload  # noqa: E402,F401
from loader import load as load_data                 # noqa: E402,F401

__all__ = ["resolve_journal_weight", "reload", "load_data"]
