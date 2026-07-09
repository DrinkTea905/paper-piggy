# -*- coding: utf-8 -*-
"""
读 zotero.sqlite 的收藏夹（Collections）→ data/categories/zotero_collections.json。
输出：收藏夹层级树(含每类文献数) + by_collection(收藏夹→item keys) + by_key(item key→收藏夹名)。
存 sidecar，不进 LanceDB 表。供「浏览」tab 按收藏夹探索文献。
用法: python build_categories.py
"""
import sys, json, sqlite3, shutil, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import zotero_source as Z

def main():
    t0 = time.time()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        import settings as S
        if S.source() != "zotero":
            print("[cat] 非 Zotero 模式，跳过收藏夹树", flush=True)
            return
    except Exception:
        pass
    d = Z.detect_data_dir()
    if not d:
        print("[cat] 未探测到 zotero.sqlite（收藏夹功能需要 Zotero）"); return
    tmp = Path(tempfile.gettempdir()) / "localkb_zotero_cat.sqlite"
    shutil.copy2(d / "zotero.sqlite", tmp)
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    cur = con.cursor()
    def q(sql, a=()): return cur.execute(sql, a).fetchall()

    LIB = 1  # 我的文库
    cols = q("SELECT collectionID, collectionName, parentCollectionID FROM collections WHERE libraryID=?", (LIB,))
    # 收藏夹 → item keys（排除已删除）
    ci = q("""SELECT ci.collectionID, i.key FROM collectionItems ci
      JOIN items i ON ci.itemID=i.itemID
      WHERE i.libraryID=? AND i.itemID NOT IN (SELECT itemID FROM deletedItems)""", (LIB,))
    con.close()

    keys_of_cid = {}
    for cid, key in ci:
        keys_of_cid.setdefault(cid, set()).add(key)

    # 建树
    node = {cid: {"id": cid, "name": name, "parent": par, "children": [], "keys": sorted(keys_of_cid.get(cid, []))}
            for cid, name, par in cols}
    roots = []
    for n in node.values():
        p = n["parent"]
        (node[p]["children"] if p in node else roots).append(n)

    def finalize(n, prefix):
        n["path"] = (prefix + " / " + n["name"]) if prefix else n["name"]
        n["count"] = len(n["keys"])
        for c in n["children"]:
            finalize(c, n["path"])
        # 直属计数 + 子孙合计
        n["count_deep"] = n["count"] + sum(c["count_deep"] for c in n["children"])
    for r in roots:
        finalize(r, "")

    # by_collection（用 path 唯一标识）+ by_key
    by_collection, by_key = {}, {}
    for n in node.values():
        by_collection[n["path"]] = n["keys"]
        for k in n["keys"]:
            by_key.setdefault(k, []).append(n["path"])

    def strip(n):
        return {"id": n["id"], "name": n["name"], "path": n["path"],
                "count": n["count"], "count_deep": n["count_deep"],
                "children": [strip(c) for c in n["children"]]}
    out = {
        "tree": [strip(r) for r in sorted(roots, key=lambda x: x["name"])],
        "by_collection": by_collection,
        "by_key": by_key,
        "n_collections": len(cols),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    C.CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
    (C.CATEGORIES_DIR / "zotero_collections.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[cat] 收藏夹 {len(cols)} 个，覆盖 {len(by_key)} 篇，用时 {time.time()-t0:.1f}s", flush=True)
    # 打印顶层几个供确认
    for r in out["tree"][:8]:
        print(f"   {r['name']}  ({r['count_deep']} 篇)")

if __name__ == "__main__":
    main()
