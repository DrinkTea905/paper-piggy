# -*- coding: utf-8 -*-
"""
一次性迁移：把 LanceDB 表的 page 列从 Null type 改成 Int64（nullable）。
根因：S 档(index_semantic)建表时 meta 行 page 全为 None → pyarrow 推断成 Null type，
      使 F 档 chunk 行(page=真实页码 int)无法 add（cast Int64→Null 失败）。
做法：读现有整表（含向量，**不重新嵌入**）→ page 列重造为 int64(全 null) → overwrite。几秒。
用法: python fix_schema.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import lancedb
import pyarrow as pa

def main():
    db = lancedb.connect(str(C.LANCEDB_DIR))
    if C.TABLE_NAME not in db.table_names():
        print("[fix] 表不存在，无需迁移"); return
    t = db.open_table(C.TABLE_NAME)
    d = t.to_arrow()
    if d.schema.field("page").type == pa.int64():
        print("[fix] page 已是 Int64，无需迁移"); return
    print(f"[fix] page 列 {d.schema.field('page').type} → int64（{len(d)} 行，复用向量不重嵌）...", flush=True)
    cols = {}
    for name in d.schema.names:
        cols[name] = pa.array([None] * len(d), type=pa.int64()) if name == "page" else d.column(name)
    new_schema = pa.schema([pa.field("page", pa.int64()) if f.name == "page" else f for f in d.schema])
    db.create_table(C.TABLE_NAME, data=pa.table(cols, schema=new_schema), mode="overwrite")
    n = db.open_table(C.TABLE_NAME)
    print(f"[fix] 完成，表 {n.count_rows()} 行，page 类型 = {n.to_arrow().schema.field('page').type}", flush=True)

if __name__ == "__main__":
    main()
