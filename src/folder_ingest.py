# -*- coding: utf-8 -*-
"""
文件夹模式 build 步骤：扫 folder_dir → 对"新/未抽"的 PDF 抽首 1-2 页 → LLM 抽题录 →
写 meta_cache（幂等/增量/并发/断点续跑）。必须先于 index_light 跑（folder 模式下 papers.jsonl 元数据依赖它）。
无 key 时正常退出（returncode 0，退回文件名题名），不阻断 LIGHT。
用法: python folder_ingest.py [--workers 3] [--limit N]
"""
import sys, os, json, time, hashlib, argparse, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import settings as S
import folder_source as FS
import folder_meta as FM
from textutil import safe_name

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# R4：多线程共享 cache 字典的锁。worker 只在持锁时写；主线程存盘前在持锁下浅拷贝快照，
# 避免 json.dumps 迭代时 worker 增键触发 "dictionary changed size during iteration" 崩溃。
_CACHE_LOCK = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load_cache():
    return FS._load_cache()


def _save_cache(cache):
    # R4：在持锁下浅拷贝快照再 dumps，防与 worker 并发写冲突。
    with _CACHE_LOCK:
        snap = dict(cache)
    C.FOLDER_DIR_STATE.mkdir(parents=True, exist_ok=True)
    tmp = C.FOLDER_META_CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(6):
        try:
            os.replace(tmp, C.FOLDER_META_CACHE)
            return
        except PermissionError:
            time.sleep(0.15 * (i + 1))
    # R4：兜底直写也必须用持锁下的快照 snap（而非活字典 cache），否则重新引入并发迭代崩溃
    C.FOLDER_META_CACHE.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")


def _file_sha1(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _purge_db_rows(keys):
    """BF10：把消失/被替换文件的 key 从 LanceDB 全量删行（meta+chunk 都删，谓词不带 row_type）。
       folder 首建时表还没有 / 连接失败 → 静默跳过（此时也没有可残留的行）。"""
    if not keys:
        return
    try:
        import lancedb
        from dbutil import key_predicate
        db = lancedb.connect(str(C.LANCEDB_DIR))
        if C.TABLE_NAME not in db.table_names():
            return
        pred = key_predicate(list(keys))
        if pred:
            db.open_table(C.TABLE_NAME).delete(pred)
    except Exception as e:
        print(f"[folder] 清理索引残留行失败（不阻断，下次重试）：{e!r}", flush=True)


def _purge_key_artifacts(keys):
    """BF10：清掉消失/被替换文件在进度文件与抽取产物里的残留。此前只剔 meta_cache，
       embedded_keys 里残留的 stem 会让"换了内容的同名文件"被当已深索永远跳过、
       表里的旧内容继续被检索命中。①进度文件按 stem 保序重写 ②删 extracted/chunks 同名产物。
       整段持 _CACHE_LOCK：ingest_one 在线程池里并发调用，无锁的读-改-写会后写覆盖前写、丢 stem。"""
    stems = {safe_name(k) for k in keys if k}
    if not stems:
        return
    with _CACHE_LOCK:
        for fname in ("embedded_keys.txt", "meta_embedded.txt", "deep_no_text.txt"):
            p = C.STATE / fname
            if not p.exists():
                continue
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                kept = [l for l in lines if l.strip() and l.strip() not in stems]
                if len(kept) != len([l for l in lines if l.strip()]):
                    p.write_text("".join(l + "\n" for l in kept), encoding="utf-8")
            except Exception:
                pass
    # 结构化提取状态也必须按 stem 清掉；否则附件替换后仍显示旧的 missing/invalid/OCR 结果。
    try:
        import deep_extract_status as DES
        DES.remove(stems)
    except Exception:
        pass
    # BF10：抽取产物 + 页码映射 sidecar（PAGEMAP_DIR）一并按 stem 删——换内容的同名文件
    # 若残留旧 pagemap，页级引注会指向别篇文章的印刷页码。
    for d in (C.EXTRACTED, C.CHUNKS, C.PAGEMAP_DIR):
        for st in stems:
            try:
                f = d / f"{st}.json"
                if f.exists():
                    f.unlink()
            except Exception:
                pass
    # BF10：SAC 摘要（summaries.json）也按 stem 剔——否则换内容的同名文件仍拼着旧文章的
    # 检索摘要做嵌入前缀，误导语义检索。持锁下原子读-改-写（并发 purge 的读-改-写会丢条目）。
    sumf = C.DATA / "summaries" / "summaries.json"
    with _CACHE_LOCK:
        try:
            if sumf.exists():
                sums = json.loads(sumf.read_text(encoding="utf-8"))
                if any(st in sums for st in stems):
                    for st in stems:
                        sums.pop(st, None)
                    tmp = sumf.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(sums, ensure_ascii=False, indent=1), encoding="utf-8")
                    os.replace(tmp, sumf)
        except Exception:
            pass


def _head_text(pdf, key):
    """取首 1-2 页文本：若深索已提取过整篇则读缓存前2页，否则现抽首2页。"""
    ex = C.EXTRACTED / f"{safe_name(key)}.json"
    if ex.exists() and ex.stat().st_size > 0:
        try:
            pages = json.loads(ex.read_text(encoding="utf-8")).get("pages") or []
        except Exception:
            pages = []
    else:
        try:
            import extract as E
            # 题录只需要首两页。必须把上限传进提取器，不能先处理整本再 [:2]；
            # 且此阶段明确关闭 OCR，避免文件夹初建题录意外 OCR 整本。扫描件仍退文件名题名，
            # 用户随后主动深索时才走本地页级 OCR。
            pages = E._extract_pages(pdf, max_pages=2, ocr_mode="off")
        except Exception:
            pages = []
    return "\n".join((p.get("text", "") if isinstance(p, dict) else str(p)) for p in pages[:2]).strip()


def ingest_one(folder, pdf, cache):
    key = FS.stable_key(folder, pdf)
    with _CACHE_LOCK:                                   # R4：持锁读，避免与其它 worker/存盘竞争
        _entry = cache.get(key) or {}
        _has_meta = bool(_entry.get("meta"))
        _old_sha = _entry.get("sha1", "")
        _no_text = _entry.get("note") == "no_text"
    if _has_meta and not _no_text:
        # BF10：skip 前比对 sha1——同路径文件被替换（key 不变、内容变）时，旧题录/旧索引
        # 全是别篇文章的，必须作废重抽。旧条目没存 sha1 维持原跳过行为（不强制重抽全部老库）。
        if not _old_sha or _file_sha1(pdf) == _old_sha:
            return "skip"
        with _CACHE_LOCK:                               # R4：持锁写
            cache.pop(key, None)
        # BF10：替换=旧内容作废——表行也删（否则旧文章的 chunk 行在下次手动深索前一直被命中）
        _purge_db_rows([key])
        _purge_key_artifacts([key])
    elif _no_text:
        # BF：曾判无正文（扫描件）的条目——存了 sha1 且内容未变则跳过（重抽仍无正文，白费）；
        # sha1 变了或旧条目没存 sha1（老版本 no_text 未记 sha1）时重试一次：内容若已可抽则
        # 升级成正式题录，否则回写 no_text（这次补上 sha1，下次即可秒判未变而跳过）。
        if _old_sha and _file_sha1(pdf) == _old_sha:
            return "skip"
        with _CACHE_LOCK:                               # R4：持锁写
            cache.pop(key, None)
    text = _head_text(pdf, key)
    if not text:
        with _CACHE_LOCK:                               # R4：持锁写
            cache[key] = {"meta": {"title": Path(pdf).stem}, "file": pdf, "sha1": _file_sha1(pdf),
                          "needs_review": True, "note": "no_text", "extracted_at": _now()}
        return "empty"
    meta, needs_review, err = FM.extract_meta(text)
    if not meta.get("title"):
        meta["title"] = Path(pdf).stem
    with _CACHE_LOCK:                                   # R4：持锁写
        cache[key] = {"meta": meta, "file": pdf, "sha1": _file_sha1(pdf),
                      "needs_review": needs_review, "extracted_at": _now(), "err": err}
    return "ok" if not err else "fallback"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    folder = S.folder_dir()
    if not folder or not Path(folder).exists():
        print("[folder] 未配置受管文件夹，跳过", flush=True)
        return
    pdfs = FS.scan(folder)
    cache = _load_cache()
    # 增量：删除的文件从 cache 剔除
    live = {FS.stable_key(folder, p) for p in pdfs}
    gone = [k for k in list(cache) if k not in live]
    # C3/D4-4：默认**不**同步删除——移出受管文件夹的 PDF 可能只是临时挪走，静默清出索引风险大。
    # 仅当用户在「设置→建库→自动更新知识库」里勾了「同步删除」才清；否则只记一笔、保留索引。
    del_sync = bool(S.load().get("auto_update", {}).get("delete_sync", False))
    if gone and not del_sync:
        print(f"[folder] 检测到 {len(gone)} 篇已移出受管文件夹，「同步删除」未开启，暂不清理"
              f"（如确要从库中移除，请在设置→建库→自动更新知识库里勾选「同步删除」）。", flush=True)
        gone = []
    if gone:
        # BF10：光剔 meta_cache 不够——表行、进度 stem、抽取产物都残留着，
        # 已删除的文献会继续被检索命中；一并清（表删行含 meta+chunk，见 _purge_db_rows）。
        # 顺序要紧：先 purge、成功后才从 cache 删 key——反了的话 purge 抛异常时 DB 旧行成孤儿，
        # 且 cache 已无记录触发下次自愈，会被持续检索命中。purge 失败则保留 key 下次重试。
        try:
            _purge_db_rows(gone)
            _purge_key_artifacts(gone)
            for k in gone:
                del cache[k]
        except Exception as e:
            print(f"[folder] 清理已删除文件残留失败（保留 cache 键下次重试）：{e!r}", flush=True)

    if not FM.available():
        print(f"[folder] 未配置 LLM，跳过题录抽取（{len(pdfs)} 篇退回文件名题名）", flush=True)
        _save_cache(cache)
        return

    # BF10：除"没题录"的新文件外，已有题录但存过 sha1 的也要送进 worker——
    # ingest_one 里比对 sha1 做替换检测（内容没变的秒 skip），否则同名替换永远检不出来。
    # BF：曾判 no_text 的条目也一并送——新条目已存 sha1（走上面 elif），老版本 no_text 未记 sha1
    # 靠 note 兜住，让其重试一次（内容变了则升级题录、没变则补上 sha1，下轮即秒跳）。
    todo, n_new = [], 0
    for p in pdfs:
        e = cache.get(FS.stable_key(folder, p)) or {}
        if not e.get("meta"):
            todo.append(p); n_new += 1
        elif e.get("sha1") or e.get("note") == "no_text":
            todo.append(p)
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print(f"[folder] 全部 {len(pdfs)} 篇题录已就绪（增量无新增）", flush=True)
        _save_cache(cache)
        return

    workers = args.workers or S.folder_meta_conf().get("workers", 3)
    print(f"[folder] 待抽题录 {n_new} 篇 + 替换检测 {len(todo) - n_new} 篇（共 {len(pdfs)} 篇），workers={workers}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(ingest_one, folder, p, cache): p for p in todo}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                print("[folder] 抽取异常：", e, flush=True)
            done += 1
            if done % 5 == 0:
                _save_cache(cache)
                print(f"[folder] {done}/{len(todo)} …", flush=True)
    _save_cache(cache)
    print(f"[folder] 题录抽取完成 {done}/{len(todo)} 篇", flush=True)


if __name__ == "__main__":
    main()
