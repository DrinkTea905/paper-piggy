# -*- coding: utf-8 -*-
"""把已下载的公开上游表机械转换为 PaperPiggy 目录。

本脚本不自行联网，便于发布前先审查上游文件和版本，再用确定的输入生成。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _best_quartile(row: dict[str, str]) -> str:
    values = []
    for key, value in row.items():
        if key.startswith("IF Quartile("):
            value = (value or "").strip().upper()
            if value in {"Q1", "Q2", "Q3", "Q4"}:
                values.append(value)
    return min(values, key=lambda q: int(q[1])) if values else ""


def build_ssci(csv_path: Path, output: Path, commit: str, checked_at: str) -> int:
    journals: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            indexes = (row.get("Web of Science") or "").upper()
            if "SSCI" not in indexes:
                continue
            name = (row.get("Journal") or "").strip()
            if not name:
                continue
            level = _best_quartile(row)
            # JCR 有收录但没有可用分区时仍保留，客观标签显示 SSCI。
            record = {
                "name": name,
                "issn": (row.get("ISSN") or row.get("EISSN") or "").strip(),
                "level": level,
            }
            old = journals.get(name.casefold())
            if old is None or (level and (not old["level"] or level < old["level"])):
                journals[name.casefold()] = record
    data = {
        "_meta": {
            "catalog": "ssci",
            "version": "JCR2025",
            "source": "hitfyd/ShowJCR JCR2025-UTF8.csv（SSCI 收录与 JCR 2025 分区）",
            "source_url": "https://github.com/hitfyd/ShowJCR",
            "upstream_commit": commit,
            "checked_at": checked_at,
            "next_check_at": "2027-01-19",
            "note": "由 update_catalogs.py 从上游原始 CSV 机械筛选；同刊多学科分区取最高分区。",
            "count": len(journals),
        },
        "journals": sorted(journals.values(), key=lambda x: x["name"].casefold()),
    }
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    return len(journals)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--showjcr-csv", type=Path, required=True)
    parser.add_argument("--showjcr-commit", required=True)
    parser.add_argument("--checked-at", default="2026-07-19")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "catalogs" / "ssci.json")
    args = parser.parse_args()
    count = build_ssci(args.showjcr_csv, args.output, args.showjcr_commit, args.checked_at)
    print(f"SSCI/JCR: {count} journals -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
