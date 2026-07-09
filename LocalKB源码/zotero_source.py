# -*- coding: utf-8 -*-
"""
直接读 Zotero 自己的 zotero.sqlite（唯一数据源，不依赖 Better BibTeX 导出）。
- 自动探测数据目录：环境变量/config 覆盖 → Zotero profile prefs.js 的 dataDir → 默认 ~/Zotero。
- Zotero 开着时库被独占锁 → 读只读副本（复制到 temp）。
- 输出建库管线所需的记录结构（含官方页码），并额外带 collections（收藏夹）。
只要用户装了 Zotero 就能用。
"""
import os, glob, re, sqlite3, shutil, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

def _clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('\n', ' ').replace('\r', ' ')).strip()

def detect_data_dir():
    """探测 Zotero 数据目录（含 zotero.sqlite）。返回 Path 或 None。"""
    # ① 显式覆盖（环境变量 / config.ZOTERO_DIR）
    env = os.environ.get("LOCALKB_ZOTERO_DIR") or getattr(C, "ZOTERO_DIR", "")
    if env and (Path(env) / "zotero.sqlite").exists():
        return Path(env)
    # ② Zotero profile 的 prefs.js 里记录的 dataDir（自定义目录的唯一可靠来源）
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    for prefs in glob.glob(os.path.join(appdata, "Zotero", "Zotero", "Profiles", "*", "prefs.js")):
        try:
            txt = open(prefs, encoding="utf-8", errors="replace").read()
            m = re.search(r'extensions\.zotero\.dataDir",\s*"([^"]+)"', txt)
            if m:
                d = Path(m.group(1).replace("\\\\", "\\"))
                if (d / "zotero.sqlite").exists():
                    return d
        except Exception:
            pass
    # ③ 默认位置
    for d in [Path(os.path.expanduser("~/Zotero")), Path.home() / "Zotero"]:
        if (d / "zotero.sqlite").exists():
            return d
    return None

def available(data_dir=None):
    d = Path(data_dir) if data_dir else detect_data_dir()
    return bool(d and (d / "zotero.sqlite").exists())

def load_papers(data_dir=None, library_id=1):
    """读 zotero.sqlite，返回 [dict]（建库记录结构 + pdf_path/collections）。
    library_id=1 = "我的文库"（排除群组库/RSS feed）。"""
    d = Path(data_dir) if data_dir else detect_data_dir()
    if not d or not (d / "zotero.sqlite").exists():
        raise FileNotFoundError("未探测到 zotero.sqlite（请确认已安装 Zotero，或手动指定目录）")
    storage = d / "storage"
    tmp = Path(tempfile.gettempdir()) / "localkb_zotero_ro.sqlite"
    # Zotero 开着时主库处于 WAL 模式：复制主库 + -wal/-shm 一起，拿到一致快照；
    # 复制失败（独占锁/权限）给清晰提示，而不是让上层静默失败。
    try:
        shutil.copy2(d / "zotero.sqlite", tmp)
        for ext in ("-wal", "-shm"):
            src = d / ("zotero.sqlite" + ext)
            if src.exists():
                try:
                    shutil.copy2(src, Path(str(tmp) + ext))
                except Exception:
                    pass
    except Exception as e:
        raise RuntimeError(f"无法读取 zotero.sqlite（{e}）——请先完全退出 Zotero 再试，或检查该文件的读取权限。")
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    cur = con.cursor()
    def q(sql, a=()): return cur.execute(sql, a).fetchall()

    items = q("""SELECT i.itemID, i.key, it.typeName FROM items i
      JOIN itemTypes it ON i.itemTypeID=it.itemTypeID
      WHERE it.typeName NOT IN ('attachment','note','annotation')
        AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        AND i.libraryID=?""", (library_id,))

    # 字段值（EAV 一次性拉全）
    fld = {}
    for iid, fn, val in q("""SELECT id.itemID, f.fieldName, idv.value FROM itemData id
      JOIN fields f ON id.fieldID=f.fieldID
      JOIN itemDataValues idv ON id.valueID=idv.valueID"""):
        fld.setdefault(iid, {})[fn] = val
    # 作者（按顺序）
    au = {}
    for iid, ln, fn in q("""SELECT ic.itemID, c.lastName, c.firstName FROM itemCreators ic
      JOIN creators c ON ic.creatorID=c.creatorID ORDER BY ic.itemID, ic.orderIndex"""):
        au.setdefault(iid, []).append(_clean((ln or "") + (fn or "")))
    # 标签 → 当作 keywords（BBT 也是把 tags 导成 keywords）
    tags = {}
    for iid, name in q("""SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON it.tagID=t.tagID"""):
        tags.setdefault(iid, []).append(name)
    # PDF 附件路径（storage:filename → storage/<附件key>/filename）
    pdf = {}
    for pid, akey, path in q("""SELECT ia.parentItemID, i2.key, ia.path FROM itemAttachments ia
      JOIN items i2 ON ia.itemID=i2.itemID
      WHERE ia.contentType='application/pdf' AND ia.parentItemID IS NOT NULL"""):
        if pid in pdf or not path:
            continue
        if path.startswith("storage:"):
            pdf[pid] = str(storage / akey / path.split(":", 1)[1])
        else:
            pdf[pid] = path  # 链接附件：绝对路径
    # 收藏夹
    colname = dict(q("SELECT collectionID, collectionName FROM collections"))
    itemcol = {}
    for cid, iid in q("SELECT collectionID, itemID FROM collectionItems"):
        if cid in colname:
            itemcol.setdefault(iid, []).append(colname[cid])
    con.close()

    papers = []
    for iid, key, typ in items:
        f = fld.get(iid, {})
        title = _clean(f.get("title") or f.get("caseName") or f.get("subject") or "")
        if not title:
            continue
        ym = re.search(r'\d{4}', f.get("date", "") or "")
        papers.append({
            "key": key,
            "title": title,
            "author": "; ".join(au.get(iid, [])),
            "year": ym.group(0) if ym else "",
            "journal": _clean(f.get("publicationTitle") or f.get("bookTitle") or f.get("proceedingsTitle") or ""),
            "doi": _clean(f.get("DOI", "")),
            "langid": _clean(f.get("language", "")),
            "keywords": "; ".join(tags.get(iid, [])),
            "abstract": _clean(f.get("abstractNote", "")),
            "itemtype": typ,
            "official_pages": _clean(f.get("pages", "")),
            "has_pdf": iid in pdf,
            "pdf_path": pdf.get(iid, ""),
            "collections": itemcol.get(iid, []),
        })
    return papers

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dd = detect_data_dir()
    print("探测到 Zotero 数据目录:", dd)
    ps = load_papers()
    print(f"读取 {len(ps)} 篇（我的文库）")
    withpdf = sum(1 for p in ps if p["has_pdf"])
    print(f"有 PDF: {withpdf}, 有摘要: {sum(1 for p in ps if p['abstract'])}, 有页码: {sum(1 for p in ps if p['official_pages'])}")
