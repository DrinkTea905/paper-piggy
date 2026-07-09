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
import sys, os, json, hashlib, tarfile, tempfile, argparse
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


def _download(url, dest, progress_cb=None, name="", expect_bytes=0):
    import requests
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) or expect_bytes
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):   # 1MB
                if not chunk:
                    continue
                f.write(chunk); done += len(chunk)
                if progress_cb:
                    progress_cb(name, done, total, "download")
    return dest


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
    """下载所有缺失模型。返回 (ok, msg)。已齐全则秒返回。"""
    miss = missing_models()
    if not miss:
        return True, "模型已就绪"
    man = _load_manifest()
    entries = {e["name"]: e for e in man.get("models", [])}
    C.MODELS.mkdir(parents=True, exist_ok=True)
    for name in miss:
        e = entries.get(name)
        if not e:
            return False, f"清单缺少 {name} 的下载信息"
        log(f"[models] 下载 {name}（约 {e.get('bytes',0)/1e6:.0f}MB）…")
        with tempfile.TemporaryDirectory(dir=str(C.MODELS)) as td:
            tgz = Path(td) / e["filename"]
            _download(e["url"], tgz, progress_cb, name, e.get("bytes", 0))
            if e.get("sha256"):
                got = _sha256(tgz, progress_cb, name)
                if got.lower() != e["sha256"].lower():
                    return False, f"{name} 校验失败（sha256 不匹配，可能下载损坏，请重试）"
            log(f"[models] 解压 {name} …")
            if progress_cb:
                progress_cb(name, 0, 0, "extract")
            with tarfile.open(tgz, "r:gz") as tf:
                tf.extractall(C.MODELS)      # 归档内顶层即 <name>/...
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
        if total:
            pct = done * 100 // total
            print(f"\r  {name} {phase} {pct}% ({done/1e6:.0f}/{total/1e6:.0f}MB)", end="", flush=True)
    ok, msg = ensure_models(progress_cb=cb)
    print("\n" + msg)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
