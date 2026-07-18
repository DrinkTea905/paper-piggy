# -*- coding: utf-8 -*-
"""
直接读 Zotero 自己的 zotero.sqlite（唯一数据源，不依赖 Better BibTeX 导出）。
- 自动探测数据目录：环境变量/config 覆盖 → Zotero profile prefs.js 的 dataDir → 默认 ~/Zotero。
- Zotero 开着时库被独占锁 → 读只读副本（复制到 temp）。
- 输出建库管线所需的记录结构（含官方页码），并额外带 collections（收藏夹）。
只要用户装了 Zotero 就能用。
"""
import os, glob, re, sqlite3, shutil, tempfile, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

def _clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('\n', ' ').replace('\r', ' ')).strip()

def _utc_to_local(s):
    """BF1：Zotero 的 dateAdded 存 UTC "YYYY-MM-DD HH:MM:SS"，直接展示会差 8 小时——转成本地时间串。"""
    try:
        return (datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        return s or ""

def _zotero_prefs_files():
    """跨平台枚举 Zotero profile 的 prefs.js（自定义 dataDir/baseAttachmentPath 的唯一可靠来源）。
       Win：%APPDATA%\\Zotero\\Zotero\\Profiles\\*；mac：~/Library/Application Support/Zotero/Profiles\\*；
       Linux：~/.zotero/zotero/*。（此前只写死了 Windows 的 %APPDATA% 路径。）"""
    if sys.platform == "darwin":
        pat = os.path.join(os.path.expanduser("~/Library/Application Support/Zotero"),
                           "Profiles", "*", "prefs.js")
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        pat = os.path.join(appdata, "Zotero", "Zotero", "Profiles", "*", "prefs.js")
    else:
        pat = os.path.join(os.path.expanduser("~/.zotero/zotero"), "*", "prefs.js")
    return glob.glob(pat)


def detect_data_dir():
    """探测 Zotero 数据目录（含 zotero.sqlite）。返回 Path 或 None。

    ★ 数据安全（2026-07-15）：只要用户「显式配置过」库（env / config.ZOTERO_DIR / settings.zotero_dir），
      就绝不回退到默认 ~/Zotero。否则配置的库一掉线（D 盘拔了 / 目录被改名），会静默落到那个早已废弃的
      空 ~/Zotero，读出几条 → 下一步 index_semantic._purge_deleted 把整库深索成果清光（零备份则不可恢复）。
      有显式配置但当前读不到时：先试 Zotero 自己的 prefs.js（能接住「用户搬过库」），仍找不到就返回 None，
      让上层 get_papers() 抛「未探测到 zotero.sqlite」安全中止 —— papers.jsonl 根本不会被改写。"""
    explicit = ""   # 非空 = 用户显式配置过库 → 绝不回退到默认 ~/Zotero
    # ① 显式覆盖（环境变量 / config.ZOTERO_DIR）——保持最高优先级：临时切库的逃生口不能被持久设置压住
    env = os.environ.get("LOCALKB_ZOTERO_DIR") or getattr(C, "ZOTERO_DIR", "")
    if env:
        explicit = env
        if (Path(env) / "zotero.sqlite").exists():
            return Path(env)
    # ①b BF28：向导里手填的 zotero_dir 落在 settings，此前从没被读过（填了等于白填）。
    # settings.py 不 import zotero_source（只 import config），此处反向 import 无循环。
    try:
        import settings as S
        sd = S.load().get("zotero_dir") or ""
    except Exception:
        sd = ""
    if sd:
        explicit = explicit or sd
        if (Path(sd) / "zotero.sqlite").exists():
            return Path(sd)
    # ② Zotero profile 的 prefs.js 里记录的 dataDir（自定义目录的唯一可靠来源；也能接住「用户搬过库」）
    for prefs in _zotero_prefs_files():
        try:
            txt = open(prefs, encoding="utf-8", errors="replace").read()
            m = re.search(r'extensions\.zotero\.dataDir",\s*"([^"]+)"', txt)
            if m:
                d = Path(m.group(1).replace("\\\\", "\\"))
                if (d / "zotero.sqlite").exists():
                    return d
        except Exception:
            pass
    # ③ 默认位置 ~/Zotero —— ★ 仅当「从未显式配置库」时才用。显式配了库却当前读不到，绝不静默换到默认空库
    #    （返回 None → get_papers() 安全中止，防「配置库掉线 → 读默认空库 → purge 误清整库」）。
    if not explicit:
        for d in [Path(os.path.expanduser("~/Zotero")), Path.home() / "Zotero"]:
            if (d / "zotero.sqlite").exists():
                return d
    return None

def _base_attachment_path():
    """读 Zotero prefs.js 的 extensions.zotero.baseAttachmentPath（链接附件 'attachments:xxx' 的相对根）。
    返回存在的 Path，或 None（未设置/目录不存在）。探测方式复用 detect_data_dir 里对 prefs.js 的解析。"""
    for prefs in _zotero_prefs_files():
        try:
            txt = open(prefs, encoding="utf-8", errors="replace").read()
            m = re.search(r'extensions\.zotero\.baseAttachmentPath",\s*"([^"]+)"', txt)
            if m:
                d = Path(m.group(1).replace("\\\\", "\\"))
                if d.exists():
                    return d
        except Exception:
            pass
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
    # BF17：临时副本改 mkstemp 唯一路径——固定文件名会让并发的 build/自动更新互踩
    # （一方读到另一方复制到一半的库，报 database disk image is malformed），用完三件套一并删除。
    fd, _tmp_name = tempfile.mkstemp(prefix="localkb_zotero_ro_", suffix=".sqlite")
    os.close(fd)
    tmp = Path(_tmp_name)
    con = None
    try:
        # Zotero 开着时主库处于 WAL 模式：复制主库 + -wal/-shm 一起，拿到一致快照；
        # 复制失败给如实提示（带原始错误），而不是让上层静默失败。
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
            raise RuntimeError(f"无法复制 zotero.sqlite 到临时目录（{e}）——请检查该文件的读取权限与磁盘剩余空间。")
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        cur = con.cursor()
        def q(sql, a=()): return cur.execute(sql, a).fetchall()

        # BF1：带出 dateAdded（真实入库时间，替代此前每次重建都被刷新的"now"）；
        # 显式 ORDER BY itemID 保证 papers.jsonl 顺序稳定（不再依赖 sqlite 的隐式返回序）。
        items = q("""SELECT i.itemID, i.key, it.typeName, i.dateAdded FROM items i
          JOIN itemTypes it ON i.itemTypeID=it.itemTypeID
          WHERE it.typeName NOT IN ('attachment','note','annotation')
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            AND i.libraryID=?
          ORDER BY i.itemID""", (library_id,))

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
        base_att = _base_attachment_path()   # 链接附件（attachments:）的相对根，解析一次复用
        for pid, akey, path in q("""SELECT ia.parentItemID, i2.key, ia.path FROM itemAttachments ia
          JOIN items i2 ON ia.itemID=i2.itemID
          WHERE ia.contentType='application/pdf' AND ia.parentItemID IS NOT NULL"""):
            if pid in pdf or not path:
                continue
            if path.startswith("storage:"):
                pdf[pid] = str(storage / akey / path.split(":", 1)[1])
            elif path.startswith("attachments:"):
                # BF：链接附件相对根 baseAttachmentPath 的形式。此前把 "attachments:xxx" 原样当路径，
                # os.path.exists 恒 False → 被误判成扫描件/需 OCR。解析成真实路径；根解析不出则
                # 不落假路径（该 pid 不入 pdf → has_pdf=False），避免把不存在的路径当成有 PDF。
                if base_att:
                    pdf[pid] = str(base_att / path.split(":", 1)[1])
            else:
                pdf[pid] = path  # 链接附件：绝对路径
        # 收藏夹
        colname = dict(q("SELECT collectionID, collectionName FROM collections"))
        itemcol = {}
        for cid, iid in q("SELECT collectionID, itemID FROM collectionItems"):
            if cid in colname:
                itemcol.setdefault(iid, []).append(colname[cid])
        con.close()
    finally:
        # BF17：临时三件套用完即删（mkstemp 不会自动清理，长期跑会在 temp 里堆尸）；
        # Windows 上连接不关文件删不掉，先兜底 close 再删。
        try:
            if con is not None:
                con.close()
        except Exception:
            pass
        for p in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    papers = []
    for iid, key, typ, dadd in items:
        f = fld.get(iid, {})
        # statute 的标题在 nameOfAct（法规名称）字段——漏读会让全部法规被当"无标题"丢弃
        title = _clean(f.get("title") or f.get("caseName") or f.get("nameOfAct") or f.get("subject") or "")
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
            # ISSN：期刊识别引擎的 ISSN 通道数据源；extra：Zotero「Extra」字段，供法条时效状态检测。
            # 两者已在上面的 EAV 全字段拉取里（fld），此处直接透传即可。
            "issn": _clean(f.get("ISSN", "")),
            "extra": _clean(f.get("extra", "")),
            "langid": _clean(f.get("language", "")),
            "keywords": "; ".join(tags.get(iid, [])),
            "abstract": _clean(f.get("abstractNote", "")),
            "itemtype": typ,
            # 全类型评价与展示所需的 Zotero 元数据。旧 papers.jsonl 没有这些键也能正常
            # 启动；下次增量/重建轻量索引时自然补齐，不要求用户先清空正式库。
            "url": _clean(f.get("url", "")),
            "website_title": _clean(f.get("websiteTitle", "")),
            "access_date": _clean(f.get("accessDate", "")),
            "publisher": _clean(f.get("publisher", "")),
            "place": _clean(f.get("place", "")),
            "isbn": _clean(f.get("ISBN", "")),
            "edition": _clean(f.get("edition", "")),
            "series": _clean(f.get("series", "")),
            "book_title": _clean(f.get("bookTitle", "")),
            "university": _clean(f.get("university", "")),
            "thesis_type": _clean(f.get("thesisType", "")),
            "institution": _clean(f.get("institution", "")),
            "report_type": _clean(f.get("reportType", "")),
            "report_number": _clean(f.get("reportNumber", "")),
            "conference_name": _clean(f.get("conferenceName", "")),
            "proceedings_title": _clean(f.get("proceedingsTitle", "")),
            "court": _clean(f.get("court", "")),
            "docket_number": _clean(f.get("docketNumber", "")),
            "decision_date": _clean(f.get("dateDecided", "")),
            "standard_number": _clean(f.get("number", "") or f.get("codeNumber", "")),
            "version": _clean(f.get("version", "")),
            "official_pages": _clean(f.get("pages", "")),
            "has_pdf": iid in pdf,
            "pdf_path": pdf.get(iid, ""),
            "collections": itemcol.get(iid, []),
            # BF1：真实入库时间 = Zotero 的 dateAdded（UTC→本地）；「最近入库」不再随每次重建全体刷新
            "ingested_at": _utc_to_local(dadd),
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
