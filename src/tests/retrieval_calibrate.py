# -*- coding: utf-8 -*-
"""来源评价加成的离线校准器。

候选快照来自正式库的只读 relevance 检索；只保留排序所需字段，不含文献正文、标题或用户设置。
``choose_scale`` 只读 calibration 组。validation 只有显式传入时才计算，防止盲测泄漏。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from retrieval_eval import GOLDSET, load_goldset


CANDIDATES = Path(__file__).with_name("retrieval_candidates_v1.jsonl")
DEFAULT_SCALES = tuple(round(i * 0.025, 3) for i in range(21))
RAW_POOL = 32                 # 生产 topk=20 时会先取 topk+12，再应用 blend 排序


def load_candidates(path: Path = CANDIDATES) -> dict[str, list[dict]]:
    rows = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        qid = item.get("query_id")
        if not qid or qid in rows or not isinstance(item.get("candidates"), list):
            raise ValueError(f"{path.name} 第 {lineno} 行结构错误或 query_id 重复")
        rows[qid] = item["candidates"]
    return rows


def validate_candidates(gold: list[dict], candidates: dict[str, list[dict]]) -> list[str]:
    issues = []
    gold_ids = {row["query_id"] for row in gold}
    if set(candidates) != gold_ids:
        issues.append("候选快照 query_id 与金标集不一致")
    for qid, items in candidates.items():
        if not items:
            issues.append(f"{qid}: 候选为空")
        for item in items:
            if not item.get("key") or not isinstance(item.get("score"), (int, float)):
                issues.append(f"{qid}: 候选缺 key/score")
                break
            weight = item.get("journal_weight")
            if weight is not None and not isinstance(weight, (int, float)):
                issues.append(f"{qid}: journal_weight 不是数值/null")
                break
    return issues


def _effective(item: dict) -> float:
    """复刻 retriever._effective 中与快照字段有关的稳定部分。"""
    score = float(item.get("score") or 0.0)
    if item.get("is_wiki"):
        if item.get("stale"):
            return score * 0.3 if score > 0 else score / 0.3
        mult = 1.0
        if str(item.get("key", "")).startswith("answer-"):
            mult *= 0.45
        if item.get("by_agent") and not item.get("verified_at"):
            mult *= 0.6
        if mult < 1.0:
            return score * mult if score > 0 else score / mult
        return score - 0.05
    if item.get("statute_status") == "已废止":
        return score * 0.5 if score > 0 else score / 0.5
    return score


def rank_candidates(items: list[dict], scale: float, topk: int = 20) -> list[dict]:
    # /search 的输出已经过 effective 排序；先按原始 score 还原生产代码 picked 的前 32 个池。
    pool = sorted(enumerate(items), key=lambda x: (-float(x[1]["score"]), x[0]))[:RAW_POOL]
    scored = []
    for original_rank, item in pool:
        bonus = 0.0 if item.get("is_wiki") else float(item.get("journal_weight") or 0.0) * scale
        scored.append((_effective(item) + bonus, original_rank, item))
    return [item for _, _, item in sorted(scored, key=lambda x: (-x[0], x[1]))[:topk]]


def _dcg(labels: list[int]) -> float:
    return sum((2 ** label - 1) / math.log2(rank + 2) for rank, label in enumerate(labels))


def ranking_metrics(gold: list[dict], candidates: dict[str, list[dict]],
                    split: str, scale: float) -> dict:
    queries = [row for row in gold if row["split"] == split and row["answerable"]]
    hit3, recalls, ndcgs, must8, weight3, weight8 = 0, [], [], [], [], []
    for row in queries:
        labels = {item["key"]: item["label"] for item in row["graded_documents"]}
        seen, ranked = set(), []
        for item in rank_candidates(candidates[row["query_id"]], scale):
            if item["key"] not in seen:
                seen.add(item["key"])
                ranked.append(item)
        top8 = ranked[:8]
        gains = [labels.get(item["key"], 0) for item in top8]
        hit3 += int(any(label >= 2 for label in gains[:3]))
        relevant = sum(label >= 2 for label in labels.values())
        recalls.append(sum(label >= 2 for label in gains) / relevant if relevant else 1.0)
        ideal = sorted(labels.values(), reverse=True)[:8]
        ndcgs.append(_dcg(gains) / (_dcg(ideal) or 1.0))
        must8.append(int(all(key in {item["key"] for item in top8} for key in row["must_hit"])))
        for bucket, top in ((weight3, ranked[:3]), (weight8, top8)):
            bucket.append(sum(float(x.get("journal_weight") or 0.0) for x in top) / (len(top) or 1))
    n = len(queries)
    avg = lambda values: sum(values) / len(values) if values else 0.0
    return {"queries": n, "hit_at_3": hit3 / n if n else 0.0,
            "recall_at_8": avg(recalls), "ndcg_at_8": avg(ndcgs),
            "must_hit_at_8": avg(must8), "mean_weight_at_3": avg(weight3),
            "mean_weight_at_8": avg(weight8)}


def off_topic_metrics(gold: list[dict], candidates: dict[str, list[dict]],
                      split: str, scale: float) -> dict:
    queries = [row for row in gold if row["split"] == split and not row["answerable"]]
    raw, combined = [], []
    for row in queries:
        top = rank_candidates(candidates[row["query_id"]], scale, topk=1)[0]
        raw.append(float(top["score"]))
        bonus = 0.0 if top.get("is_wiki") else float(top.get("journal_weight") or 0.0) * scale
        combined.append(_effective(top) + bonus)
    avg = lambda values: sum(values) / len(values) if values else 0.0
    return {"queries": len(queries), "mean_raw_top_score": avg(raw),
            "max_raw_top_score": max(raw, default=0.0),
            "mean_combined_top_score": avg(combined),
            "max_combined_top_score": max(combined, default=0.0)}


def choose_scale(gold: list[dict], candidates: dict[str, list[dict]],
                 scales: tuple[float, ...] = DEFAULT_SCALES) -> dict:
    """只用 calibration：先保 Recall@8，再比 Hit@3、nDCG@8；完全同分取更小加成。"""
    table = [{"scale": scale, **ranking_metrics(gold, candidates, "calibration", scale),
              "off_topic": off_topic_metrics(gold, candidates, "calibration", scale)}
             for scale in scales]
    best = max(table, key=lambda row: (row["recall_at_8"], row["hit_at_3"],
                                       row["ndcg_at_8"], -row["scale"]))
    return {"chosen": best, "grid": table}


def main() -> None:
    parser = argparse.ArgumentParser(description="离线校准来源评价加成（默认绝不读取 validation）")
    parser.add_argument("--goldset", type=Path, default=GOLDSET)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--validate", type=float, default=None,
                        help="显式打开盲测组并评估指定 scale；选择 scale 时不要传")
    args = parser.parse_args()
    gold, candidates = load_goldset(args.goldset), load_candidates(args.candidates)
    issues = validate_candidates(gold, candidates)
    if issues:
        raise ValueError("候选快照校验失败：\n- " + "\n- ".join(issues))
    if args.validate is None:
        output = choose_scale(gold, candidates)
    else:
        output = {"scale": args.validate,
                  "validation": ranking_metrics(gold, candidates, "validation", args.validate),
                  "off_topic": off_topic_metrics(gold, candidates, "validation", args.validate)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
