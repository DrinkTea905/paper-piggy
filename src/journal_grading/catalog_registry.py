# -*- coding: utf-8 -*-
"""期刊目录来源信息的开发维护事实源。

目录内容仍由 ``catalogs/*.json`` 提供；本模块只补齐每个目录共同需要的
来源 URL、上游版本/提交和检查日期，供开发维护和目录溯源使用。这里不改
用户手动档位，也不会联网自动覆盖目录；这些维护信息不进入普通用户界面。
"""
from __future__ import annotations

import json
from pathlib import Path

PKG = Path(__file__).parent
CHECKED_AT = "2026-07-19"
NEXT_CHECK_AT = "2027-01-19"

CNKI_COMMIT = "477f3e10877b394dec5f06a9a750bcb18d9a9979"
SHOWJCR_COMMIT = "1e537a2717b81e6595b70c493d86adf2c4612934"

# 目录数据有的来自公开上游、有的是项目内维护的偏好表。后者仍记录项目文件
# 的稳定 URL，避免 UI 出现“来源未知”，并明确不把个人目录冒充官方名录。
UPSTREAM = {
    "cssci": ("https://github.com/fenqijun/cnki-Scholar", CNKI_COMMIT),
    "pku": ("https://github.com/fenqijun/cnki-Scholar", CNKI_COMMIT),
    "if_cnki": ("https://github.com/fenqijun/cnki-Scholar", CNKI_COMMIT),
    "ssci": ("https://github.com/hitfyd/ShowJCR", SHOWJCR_COMMIT),
    "warning": ("https://github.com/hitfyd/ShowJCR", SHOWJCR_COMMIT),
    "sjr": ("https://www.scimagojr.com/journalrank.php", "SJR 2024"),
    "tssci_law": (
        "https://liberal.ncu.edu.tw/xhr/announcements/file/67762fcd0857770928b447d1/2024%E5%B9%B4%E8%A9%95%E6%AF%94%E7%B5%90%E6%9E%9C%E6%9A%A8%E6%A0%B8%E5%BF%83%E6%9C%9F%E5%88%8A%E5%90%8D%E5%96%AE.pdf",
        "2024（适用 2025-01-01 至 2027-12-31）",
    ),
    "clsci": ("https://www.fxcxw.org.cn/", "2024 seed"),
    "ami": ("https://www.cass.cn/", "seed"),
    "ahci": ("https://mjl.clarivate.com/", "seed"),
    "erih": ("https://kanalregister.hkdir.no/publiseringskanaler/erihplus/", "seed"),
    "fms": ("https://www.nsfc.gov.cn/", "seed"),
    "ft50": ("https://www.ft.com/content/3405a512-5cbb-11e1-8f1f-00144feabdc0", "seed"),
    "utd24": ("https://jsom.utdallas.edu/the-utd-top-100-business-school-research-rankings/", "seed"),
    "abs": ("https://charteredabs.org/academic-journal-guide-2024/", "AJG 2024 seed"),
    "law_review_top": ("https://github.com/DrinkTea905/paper-piggy", "project preference 2026-06-28"),
    "ssci_law_authority": ("https://github.com/DrinkTea905/paper-piggy", "project preference 2026-06-28"),
    "tw_law": ("https://github.com/DrinkTea905/paper-piggy", "personal preference 2026-06-28"),
    "newspaper": ("https://github.com/DrinkTea905/paper-piggy", "project preference 2026-06-28"),
}


def records():
    """返回所有随包目录的可展示来源记录；坏文件只标错误，不阻塞应用。"""
    out = []
    for path in sorted((PKG / "catalogs").glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            meta = raw.get("_meta") or {}
            catalog = meta.get("catalog") or path.stem
            url, upstream = UPSTREAM.get(catalog, ("https://github.com/DrinkTea905/paper-piggy", meta.get("version", "")))
            out.append({
                "catalog": catalog,
                "source": meta.get("source", ""),
                "source_url": meta.get("source_url") or url,
                "upstream_version": meta.get("upstream_commit") or upstream or meta.get("version", ""),
                "catalog_version": meta.get("version", ""),
                "last_checked": meta.get("last_checked") or meta.get("checked_at") or CHECKED_AT,
                "next_check": meta.get("next_check") or meta.get("next_check_at") or NEXT_CHECK_AT,
                "count": len(raw.get("journals") or []),
                "private": catalog in {"law_review_top", "ssci_law_authority", "tw_law", "newspaper"},
                "official": catalog == "tssci_law",
                "note": meta.get("note", ""),
            })
        except Exception as exc:
            out.append({"catalog": path.stem, "error": str(exc), "last_checked": CHECKED_AT,
                        "next_check": NEXT_CHECK_AT})
    return out


def summary():
    rows = records()
    due = [r for r in rows if (r.get("next_check") or "9999") <= CHECKED_AT]
    return {"frequency": "每半年", "last_checked": CHECKED_AT, "next_check": NEXT_CHECK_AT,
            "due": len(due), "catalogs": rows}
