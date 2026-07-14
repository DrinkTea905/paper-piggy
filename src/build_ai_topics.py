# -*- coding: utf-8 -*-
"""
AI 主题聚类：把全库文献按 bge-m3 向量做 KMeans 聚类，给每簇起个主题名（jieba 高频词）。
用途：补 Zotero 收藏夹没归类的散篇、给跨收藏夹的主题鸟瞰。存 data/categories/ai_topics.json。
- 每篇一个代表向量：深索篇取其块向量均值，仅题录篇取 meta 向量。
- 命名：簇内文献 标题+关键词 用 jieba(法律词典)分词，取高频 top 词。零 LLM、全自动。
用法: python build_ai_topics.py [--k 24]
"""
import sys, json, re, time, argparse
from pathlib import Path
from collections import Counter, defaultdict
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import numpy as np
import lancedb
from textutil import tokenize

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--k", type=int, default=0)  # 0=按篇数自适应
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()

    t = lancedb.connect(str(C.LANCEDB_DIR)).open_table(C.TABLE_NAME)
    d = t.to_arrow()
    cids = d.column("chunk_id").to_pylist()
    keys = d.column("key").to_pylist()
    vecs = d.column("vector").to_pylist()
    # 每篇代表向量 = 该 key 所有行向量的均值。
    # 综合层 wiki 行（chunk_id 以 "::wiki" 结尾）不参与文献聚类，否则会污染主题簇、且它不在 papers.jsonl。
    acc = defaultdict(lambda: [np.zeros(C.EMBED_DIM, np.float32), 0])
    for cid, k, v in zip(cids, keys, vecs):
        if str(cid).endswith("::wiki"):
            continue
        acc[k][0] += np.asarray(v, np.float32); acc[k][1] += 1
    K = list(acc.keys())
    X = np.vstack([acc[k][0] / max(1, acc[k][1]) for k in K]).astype(np.float32)
    # 簇数自适应：小库少切、大库封顶 24，避免小库切出一堆碎主题（F9）。--k>0 时用指定值。
    k = args.k if args.k > 0 else max(2, min(24, len(K) // 15 or 2))
    k = min(k, len(K))
    print(f"[topics] {len(K)} 篇代表向量，KMeans k={k} ...", flush=True)

    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=k, n_init=8, random_state=42).fit(X)
    labels = km.labels_

    papers = {}
    for line in open(C.PAPERS_JSONL, encoding="utf-8"):
        if line.strip():
            p = json.loads(line); papers[p["key"]] = p

    STOP_SHORT = set("研究 制度 问题 分析 视角 视野 探讨 论 中国 我国 基于 影响 关系 实证 考察 路径 完善 构建 机制 一个 以及 及其".split())
    EN_STOP = set(("of the and in is a to on for with by at as an be this that are was were from or its "
                   "their we our study studies analysis research review based effect between using role "
                   "toward how what which who not can does do it he she they there here also into more "
                   "case cases法 law legal social public").split())
    topics = []
    for c in range(km.n_clusters):
        ks = [K[i] for i in range(len(K)) if labels[i] == c]
        cnt = Counter()
        for k in ks:
            p = papers.get(k, {})
            for w in tokenize(p.get("title", "") + " " + (p.get("keywords", "") or "")):
                wl = w.lower()
                if len(w) < 2 or w in STOP_SHORT or wl in EN_STOP:
                    continue
                cnt[w] += 2 if re.search(r'[一-鿿]', w) else 1  # 中文词优先（权重加倍）
        name = " · ".join(w for w, _ in cnt.most_common(3)) or f"主题{c+1}"
        topics.append({"id": c, "name": name, "size": len(ks), "keys": ks})
    topics.sort(key=lambda x: -x["size"])

    out = {
        "topics": topics,
        "by_key": {K[i]: int(labels[i]) for i in range(len(K))},
        "k": km.n_clusters,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    C.CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
    (C.CATEGORIES_DIR / "ai_topics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[topics] {km.n_clusters} 个主题，用时 {time.time()-t0:.0f}s：", flush=True)
    for t_ in topics[:12]:
        print(f"   {t_['name']}  ({t_['size']} 篇)")

if __name__ == "__main__":
    main()
