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
import sys, os, json, hashlib, tarfile, argparse, shutil, time
from pathlib import Path, PurePosixPath
sys.path.insert(0, str(Path(__file__).parent))
import config as C

MANIFEST = Path(__file__).parent / "models_manifest.json"
NEEDED = ("bge-m3-onnx", "bge-reranker-v2-m3-onnx")   # 运行时必需的两个模型目录
MODEL_FILES = ("model_quantized.onnx", "config.json", "ort_config.json",
               "sentencepiece.bpe.model", "special_tokens_map.json",
               "tokenizer.json", "tokenizer_config.json")
READY_MARKER = ".paperpiggy-model-ready.json"


def model_present(name):
    root = C.MODELS / name
    try:
        complete = all((root / fn).is_file() and (root / fn).stat().st_size > 0
                       for fn in MODEL_FILES)
    except OSError:
        return False
    if not complete:
        return False
    marker = root / READY_MARKER
    if not marker.exists():
        return True                 # 兼容早期已完整下载、但还没有完成标记的模型
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        archive_sha = str(data.get("archive_sha256") or "").lower()
        return (data.get("schema") == 1 and data.get("name") == name
                and len(archive_sha) == 64
                and all(c in "0123456789abcdef" for c in archive_sha)
                and int(data.get("archive_bytes") or 0) > 0)
    except Exception:
        return False


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


def _unique_path(parent, stem):
    p = Path(parent) / f"{stem}-{os.getpid()}-{time.time_ns()}"
    if p.exists():
        raise RuntimeError(f"暂存路径意外已存在：{p}")
    return p


def _safe_extract_model(archive, staging, name, expect_archive_bytes=0):
    """只允许归档内出现 <name>/ 下的普通文件和目录，拒绝穿越与链接。"""
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        if not members:
            raise RuntimeError("模型归档为空")
        total = 0
        for m in members:
            rel = PurePosixPath(m.name.replace("\\", "/"))
            if (rel.is_absolute() or ".." in rel.parts or not rel.parts
                    or rel.parts[0] != name or any(":" in part for part in rel.parts)):
                raise RuntimeError(f"归档含不安全或越界路径：{m.name!r}")
            if m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                raise RuntimeError(f"归档含链接或特殊文件：{m.name!r}")
            total += int(m.size or 0)
        # 模型压缩率很低；给足余量，同时挡住异常膨胀归档。
        if expect_archive_bytes and total > max(expect_archive_bytes * 5, 2_000_000_000):
            raise RuntimeError(f"归档解压体积异常：{total} 字节")
        for m in members:
            rel = PurePosixPath(m.name.replace("\\", "/"))
            dst = staging.joinpath(*rel.parts)
            if m.isdir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                raise RuntimeError(f"无法读取归档成员：{m.name!r}")
            with src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out, length=1 << 20)

    root = staging / name
    missing = [fn for fn in MODEL_FILES
               if not (root / fn).is_file() or (root / fn).stat().st_size <= 0]
    if missing:
        raise RuntimeError("归档缺少运行时文件：" + ", ".join(missing))
    return root


def _install_staged_model(staged_model, name):
    """同盘改名原子落位；失败时尽力恢复原有不完整目录并保留现场。"""
    target = C.MODELS / name
    old = None
    if target.exists():
        old = _unique_path(C.MODELS, f".{name}.incomplete")
        os.rename(target, old)
    try:
        os.rename(staged_model, target)
        if not model_present(name):
            raise RuntimeError("模型落位后完整性复检失败")
    except Exception:
        if target.exists():
            failed = _unique_path(C.MODELS, f".{name}.failed")
            os.rename(target, failed)
        if old and old.exists() and not target.exists():
            os.rename(old, target)
        raise
    if old and old.exists():
        shutil.rmtree(old)


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
        expected_sha = str(e.get("sha256") or "").lower()
        try:
            expected_bytes = int(e.get("bytes") or 0)
        except (TypeError, ValueError):
            expected_bytes = 0
        if len(expected_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_sha):
            return False, f"清单里 {name} 缺少有效 sha256，已拒绝未校验模型"
        if expected_bytes <= 0:
            return False, f"清单里 {name} 缺少有效文件大小，已拒绝未校验模型"
        actual_bytes = part.stat().st_size
        if actual_bytes != expected_bytes:
            part.unlink(missing_ok=True)
            return False, (f"{name} 校验失败（大小应为 {expected_bytes}，实际 {actual_bytes}；"
                           "已清除损坏断点，请重试）")
        got = _sha256(part, progress_cb, name)
        if got.lower() != expected_sha:
            part.unlink(missing_ok=True)
            return False, f"{name} 校验失败（sha256 不匹配，可能下载损坏，已清除请重试）"
        log(f"[models] 解压 {name} …")
        if progress_cb:
            progress_cb(name, 0, -1, "extract")   # total=-1 → 前端识别为 indeterminate（见函数 docstring）
        staging = _unique_path(C.MODELS, f".{name}.staging")
        try:
            staged_model = _safe_extract_model(part, staging, name, expected_bytes)
            (staged_model / READY_MARKER).write_text(json.dumps({
                "schema": 1, "name": name, "archive_sha256": got,
                "archive_bytes": actual_bytes,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            _install_staged_model(staged_model, name)
        except Exception as ex:
            # 仅清理由本次创建且尚未落位的 staging；正式目录和失败现场不碰。
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            return False, f"{name} 安全解压或落位失败（原模型未被半成品覆盖）：{ex}"
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        part.unlink(missing_ok=True)       # 完整校验并原子落位成功后才删断点文件
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
