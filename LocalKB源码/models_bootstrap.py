# -*- coding: utf-8 -*-
"""
首启模型下载（分发版）。运行期只需两个 INT8 量化 ONNX 模型（各 ~590MB，合计 ~1.2GB）。
分发包不内置模型；首次启动时从 manifest 指定的云端 URL 下载到 config.MODELS 下。
- 检测：MODELS/<name>/model_quantized.onnx 是否存在。
- 下载：流式下载 .tar.gz → 校验 sha256/大小 → 解压到 MODELS/<name>/。
- 进度：progress_cb(name, done_bytes, total_bytes, phase) 回调（供 UI SSE）。
manifest 文件 `models_manifest.json` 与本脚本同目录，字段见 pack_models.py 生成。
用法(CLI)：python models_bootstrap.py         # 下载缺失模型
          python models_bootstrap.py --check  # 只报告是否齐全（exit 0=齐全,1=缺）
"""
import sys, os, json, hashlib, tarfile, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

MANIFEST = Path(__file__).parent / "models_manifest.json"
NEEDED = ("bge-m3-onnx", "bge-reranker-v2-m3-onnx")   # 运行时必需的两个模型目录


def model_present(name):
    return (C.MODELS / name / "model_quantized.onnx").exists()


def missing_models():
    return [n for n in NEEDED if not model_present(n)]


def models_present():
    return not missing_models()


def _load_manifest():
    if not MANIFEST.exists():
        raise FileNotFoundError(f"缺少模型清单 {MANIFEST}；分发包应内置该文件（由 pack_models.py 生成）")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _urls_for(e):
    """从 manifest 条目取候选下载直链列表：优先 urls/mirrors 数组，回退单个 url。
    多镜像：GitHub Release 国内易失败，可在清单里给多个镜像地址，按序尝试。
    含 "<" 的一律丢弃（pack_models 的占位符如 <用户名>）——否则会拿它去发一次注定 404 的请求，
    还把真实原因（清单没填直链）伪装成一个莫名其妙的网络错误。丢干净后调用方才好报「没有可用地址」。"""
    us = e.get("urls") or e.get("mirrors")
    if not (isinstance(us, list) and us):
        u = e.get("url")
        us = [u] if u else []
    return [u for u in us if u and "<" not in u]


def _free_bytes(path):
    import shutil
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:
        return None


def _download(urls, dest, progress_cb=None, name="", expect_bytes=0):
    """多镜像 + HTTP Range 断点续传下载到 dest。
    - urls: 候选直链列表，按序尝试，任一成功即返回；全失败抛最后一个异常。
    - 断点续传：dest 已有部分字节则带 Range 从断点续下；失败时保留已下字节，
      下次（同一或下一镜像）继续，不从 0 重来。"""
    import requests
    last_err = None
    for url in urls:
        try:
            done = dest.stat().st_size if dest.exists() else 0
            headers = {"Range": f"bytes={done}-"} if done else {}
            with requests.get(url, stream=True, timeout=60, headers=headers) as r:
                if r.status_code == 416 and done:
                    return dest        # Range 越界＝该文件已下完
                if done and r.status_code == 200:
                    done = 0; mode = "wb"   # 服务器不支持 Range → 从头覆盖重下
                elif r.status_code in (200, 206):
                    mode = "ab" if done else "wb"
                else:
                    r.raise_for_status()
                    mode = "wb"
                clen = int(r.headers.get("content-length", 0))
                total = (done + clen) if clen else expect_bytes   # 已下 + 本次剩余
                with open(dest, mode) as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):   # 1MB
                        if not chunk:
                            continue
                        f.write(chunk); done += len(chunk)
                        if progress_cb:
                            progress_cb(name, done, total, "download")
            return dest
        except Exception as ex:
            last_err = ex
            continue
    raise last_err or RuntimeError("下载失败：清单未提供可用下载地址")


def _sha256(path, progress_cb=None, name=""):
    h = hashlib.sha256()
    size = path.stat().st_size; done = 0
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b); done += len(b)
            if progress_cb:
                progress_cb(name, done, size, "verify")
    return h.hexdigest()


def ensure_models(progress_cb=None, log=print):
    """下载所有缺失模型。返回 (ok, msg)。已齐全则秒返回。

    进度回调 progress_cb(name, done, total, phase) 语义：
      phase="download"/"verify" 时 total>0，done/total 为真实百分比；
      phase="extract" 时 total 传 **-1**（解压无精确进度）——前端据此渲染为
      indeterminate（不确定进度）条纹动画，而不是错误地显示「0% / 0.0MB 卡死」。"""
    miss = missing_models()
    if not miss:
        return True, "模型已就绪"
    man = _load_manifest()
    entries = {e["name"]: e for e in man.get("models", [])}
    C.MODELS.mkdir(parents=True, exist_ok=True)
    # 磁盘空间预检：需同时容纳 .tar.gz 与解压后文件，粗估按缺失总字节的 2.2 倍
    need_bytes = sum(int(entries.get(n, {}).get("bytes", 0) or 0) for n in miss)
    free = _free_bytes(C.MODELS)
    if need_bytes and free is not None and free < int(need_bytes * 2.2):
        return False, (f"磁盘空间不足：下载+解压约需 {need_bytes*2.2/1e9:.1f}GB 可用空间，"
                       f"当前仅 {free/1e9:.1f}GB。请清理磁盘后重试，或改用 API 模式（无需下载模型）。")
    for name in miss:
        e = entries.get(name)
        if not e:
            return False, f"清单缺少 {name} 的下载信息"
        urls = _urls_for(e)
        if not urls:
            return False, f"清单里 {name} 没有可用下载地址（url/urls 为空或仍是占位符）"
        log(f"[models] 下载 {name}（约 {e.get('bytes',0)/1e6:.0f}MB，{len(urls)} 个镜像）…")
        # 断点续传落持久 .part 文件（不用 TemporaryDirectory，否则重启即丢、无法续传）
        part = C.MODELS / (e["filename"] + ".part")
        try:
            _download(urls, part, progress_cb, name, e.get("bytes", 0))
        except Exception as ex:
            return False, f"{name} 下载失败（已保留断点，下次续传）：{ex}"
        if e.get("sha256"):
            got = _sha256(part, progress_cb, name)
            if got.lower() != e["sha256"].lower():
                try:
                    part.unlink()   # 校验失败＝已损坏，删掉以便下次全新重下
                except Exception:
                    pass
                return False, f"{name} 校验失败（sha256 不匹配，可能下载损坏，已清除请重试）"
        log(f"[models] 解压 {name} …")
        if progress_cb:
            progress_cb(name, 0, -1, "extract")   # total=-1 → 前端识别为 indeterminate（见函数 docstring）
        with tarfile.open(part, "r:gz") as tf:
            tf.extractall(C.MODELS)      # 归档内顶层即 <name>/...
        try:
            part.unlink()                # 解压成功即删断点文件
        except Exception:
            pass
        if not model_present(name):
            return False, f"{name} 解压后仍缺 model_quantized.onnx（归档结构不符）"
        log(f"[models] {name} 就绪 ✓")
    return True, "全部模型下载完成"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查是否齐全")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if args.check:
        miss = missing_models()
        print("齐全" if not miss else "缺少: " + ", ".join(miss))
        sys.exit(0 if not miss else 1)

    def cb(name, done, total, phase):
        if total and total > 0:
            pct = done * 100 // total
            print(f"\r  {name} {phase} {pct}% ({done/1e6:.0f}/{total/1e6:.0f}MB)", end="", flush=True)
        else:
            print(f"\r  {name} {phase} …（进行中）", end="", flush=True)   # total=-1：解压等无精确进度
    ok, msg = ensure_models(progress_cb=cb)
    print("\n" + msg)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
