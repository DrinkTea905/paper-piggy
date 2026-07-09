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
    C.FOLDER_META_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _file_sha1(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


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
            pages = E._extract_pages(pdf)[:2]
        except Exception:
            pages = []
    return "\n".join((p.get("text", "") if isinstance(p, dict) else str(p)) for p in pages[:2]).strip()


def ingest_one(folder, pdf, cache):
    key = FS.stable_key(folder, pdf)
    with _CACHE_LOCK:                                   # R4：持锁读，避免与其它 worker/存盘竞争
        if key in cache and cache[key].get("meta"):
            return "skip"
    text = _head_text(pdf, key)
    if not text:
        with _CACHE_LOCK:                               # R4：持锁写
            cache[key] = {"meta": {"title": Path(pdf).stem}, "file": pdf,
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
    for k in list(cache):
        if k not in live:
            del cache[k]

    if not FM.available():
        print(f"[folder] 未配置 LLM，跳过题录抽取（{len(pdfs)} 篇退回文件名题名）", flush=True)
        _save_cache(cache)
        return

    todo = [p for p in pdfs
            if FS.stable_key(folder, p) not in cache or not cache[FS.stable_key(folder, p)].get("meta")]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print(f"[folder] 全部 {len(pdfs)} 篇题录已就绪（增量无新增）", flush=True)
        _save_cache(cache)
        return

    workers = args.workers or S.folder_meta_conf().get("workers", 3)
    print(f"[folder] 待抽题录 {len(todo)}/{len(pdfs)} 篇，workers={workers}", flush=True)
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
