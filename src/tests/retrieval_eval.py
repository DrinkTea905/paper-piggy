# -*- coding: utf-8 -*-
"""PaperPiggy 检索金标集的纯离线指标计算。

不导入应用、不连接 MCP、不调用模型。默认只看 calibration，防止调参时偷看
validation；只有显式传 ``split='all'`` 或命令行 ``--split all`` 才会计算全量。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable


GOLDSET = Path(__file__).with_name("retrieval_goldset_v1.jsonl")
SORTS = ("relevance", "blend")
SPLITS = ("calibration", "validation")
REQUIRED_FIELDS = {
    "query_id", "query", "topic", "query_type", "language", "answerable",
    "relevance_results", "blend_results", "graded_documents", "must_hit",
    "evidence_pages", "label_reason", "wiki_hits", "needs_user_judgment", "split",
}


def load_goldset(path: Path = GOLDSET) -> list[dict]:
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} 第 {lineno} 行不是有效 JSON: {exc}") from exc
    return rows


def validate_goldset(rows: list[dict]) -> list[str]:
    """返回所有结构问题；空列表表示可用于回归。"""
    issues: list[str] = []
    ids = [row.get("query_id") for row in rows]
    if len(rows) != 38:
        issues.append(f"查询数应为 38，实际 {len(rows)}")
    if len(set(ids)) != len(ids):
        issues.append("query_id 存在重复")
    if sum(bool(row.get("answerable")) for row in rows) != 30:
        issues.append("可回答查询数应为 30")
    if sum(not bool(row.get("answerable")) for row in rows) != 8:
        issues.append("离题查询数应为 8")
    split_counts = {name: sum(row.get("split") == name for row in rows) for name in SPLITS}
    if split_counts != {"calibration": 25, "validation": 13}:
        issues.append(f"数据分组应为 calibration=25/validation=13，实际 {split_counts}")

    for row in rows:
        qid = row.get("query_id", "<缺失>")
        missing = sorted(REQUIRED_FIELDS - set(row))
        if missing:
            issues.append(f"{qid}: 缺少字段 {', '.join(missing)}")
            continue
        if row["split"] not in SPLITS:
            issues.append(f"{qid}: split 非法：{row['split']}")
        labels = {}
        for item in row["graded_documents"]:
            key, label = item.get("key"), item.get("label")
            if not key or label not in (0, 1, 2, 3):
                issues.append(f"{qid}: graded_documents 含非法 key/label")
                continue
            if key in labels:
                issues.append(f"{qid}: graded_documents 重复 key={key}")
            labels[key] = label
        high = {key for key, label in labels.items() if label >= 2}
        evidence = {item.get("key") for item in row["evidence_pages"]}
        if row["answerable"] and not high:
            issues.append(f"{qid}: 可回答查询没有 2/3 分文献")
        if not row["answerable"] and any(label > 0 for label in labels.values()):
            issues.append(f"{qid}: 离题查询含正标签")
        if high - evidence:
            issues.append(f"{qid}: 2/3 分文献缺页证 {sorted(high - evidence)}")
        if set(row["must_hit"]) - high:
            issues.append(f"{qid}: must_hit 不是 2/3 分文献")
        for sort in SORTS:
            result_key = f"{sort}_results"
            for item in row[result_key]:
                if not item.get("key") or not isinstance(item.get("score"), (int, float)):
                    issues.append(f"{qid}: {result_key} 含非法结果")
    return issues


def _select(rows: Iterable[dict], split: str) -> list[dict]:
    if split == "all":
        return list(rows)
    if split not in SPLITS:
        raise ValueError(f"split 必须是 all/{'/'.join(SPLITS)}")
    return [row for row in rows if row["split"] == split]


def _dedupe(results: Iterable[dict]) -> list[dict]:
    seen, output = set(), []
    for item in results:
        key = item.get("key")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _dcg(labels: Iterable[int]) -> float:
    return sum((2 ** label - 1) / math.log2(rank + 2)
               for rank, label in enumerate(labels))


def ranking_metrics(rows: list[dict], sort: str, split: str = "calibration") -> dict:
    if sort not in SORTS:
        raise ValueError(f"sort 必须是 {'/'.join(SORTS)}")
    queries = [row for row in _select(rows, split) if row["answerable"]]
    hit3, recalls, ndcgs, unique8 = 0, [], [], []
    for row in queries:
        labels = {item["key"]: item["label"] for item in row["graded_documents"]}
        raw = row[f"{sort}_results"]
        ranked = _dedupe(raw)[:8]
        gains = [labels.get(item["key"], 0) for item in ranked]
        if any(label >= 2 for label in gains[:3]):
            hit3 += 1
        relevant = sum(label >= 2 for label in labels.values())
        recalls.append(sum(label >= 2 for label in gains) / relevant if relevant else 1.0)
        ideal = sorted(labels.values(), reverse=True)[:8]
        ndcgs.append(_dcg(gains) / (_dcg(ideal) or 1.0))
        # 与首轮基线口径一致：原始前 8 个结果中，不重复文献所占的固定 8 个槽位比例。
        unique8.append(len({item["key"] for item in raw[:8]}) / 8)
    n = len(queries)
    return {
        "queries": n,
        "hit_at_3": hit3 / n if n else 0.0,
        "recall_at_8": sum(recalls) / n if n else 0.0,
        "ndcg_at_8": sum(ndcgs) / n if n else 0.0,
        "unique_at_8": sum(unique8) / n if n else 0.0,
    }


def off_topic_metrics(rows: list[dict], sort: str, split: str = "calibration",
                      threshold: float | None = None) -> dict:
    queries = [row for row in _select(rows, split) if not row["answerable"]]
    top_scores = []
    for row in queries:
        scores = [float(item["score"]) for item in row[f"{sort}_results"]]
        top_scores.append(max(scores, default=0.0))
    output = {
        "queries": len(queries),
        "max_top_score": max(top_scores, default=0.0),
        "mean_top_score": sum(top_scores) / len(top_scores) if top_scores else 0.0,
        "top_scores": top_scores,
    }
    if threshold is not None:
        output["rejection_rate"] = (
            sum(score < threshold for score in top_scores) / len(top_scores) if top_scores else 0.0
        )
    return output


def blend_shifts(rows: list[dict], split: str = "calibration") -> dict:
    """比较 2/3 分文献在原始 relevance/blend 排名中的位移。"""
    items = []
    for row in [x for x in _select(rows, split) if x["answerable"]]:
        positions = {}
        for sort in SORTS:
            first = {}
            for item in row[f"{sort}_results"]:
                first.setdefault(item["key"], item["rank"])
            positions[sort] = first
        for graded in row["graded_documents"]:
            key = graded["key"]
            if graded["label"] < 2 or key not in positions["relevance"] or key not in positions["blend"]:
                continue
            before, after = positions["relevance"][key], positions["blend"][key]
            items.append({"query_id": row["query_id"], "key": key,
                          "relevance_rank": before, "blend_rank": after,
                          "delta": after - before})
    return {
        "comparable": len(items),
        "down": sum(item["delta"] > 0 for item in items),
        "up": sum(item["delta"] < 0 for item in items),
        "same": sum(item["delta"] == 0 for item in items),
        "mean_delta": sum(item["delta"] for item in items) / len(items) if items else 0.0,
        "largest_demotions": sorted(items, key=lambda item: item["delta"], reverse=True)[:10],
    }


def report(rows: list[dict], split: str = "calibration", threshold: float | None = None) -> dict:
    issues = validate_goldset(rows)
    if issues:
        raise ValueError("金标集校验失败：\n- " + "\n- ".join(issues))
    return {
        "split": split,
        "ranking": {sort: ranking_metrics(rows, sort, split) for sort in SORTS},
        "off_topic": {sort: off_topic_metrics(rows, sort, split, threshold) for sort in SORTS},
        "blend_shifts": blend_shifts(rows, split),
        "ambiguous_queries": [row["query_id"] for row in _select(rows, split)
                              if row["needs_user_judgment"].get("needed")],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="离线计算 PaperPiggy 检索金标指标")
    parser.add_argument("--goldset", type=Path, default=GOLDSET)
    parser.add_argument("--split", choices=("calibration", "validation", "all"),
                        default="calibration")
    parser.add_argument("--threshold", type=float, default=None,
                        help="仅试算离题拒绝率，不会修改应用阈值")
    args = parser.parse_args()
    print(json.dumps(report(load_goldset(args.goldset), args.split, args.threshold),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
