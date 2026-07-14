# -*- coding: utf-8 -*-
"""
打包分发用的「瘦模型」资产（开发机跑一次）。
每个模型只保留运行时需要的文件（INT8 量化 onnx + tokenizer），排除 fp32 的 model.onnx_data(2.27GB)。
产出 dist/model_assets/<name>.tar.gz（各 ~473MB，解压后各 ~590MB）+ models_manifest.json（含 sha256/大小/下载直链）。
之后：把两个 .tar.gz 传到 GitHub Release（tag=MODELS_TAG），models_manifest.json 随分发包一起发
      （models_bootstrap.py 首启据此下载）。

用法:
  python pack_models.py                    # 打 tar.gz + 生成清单
  python pack_models.py --manifest-only    # 只生成清单，不落盘 1.2GB 大包（见下「可复现归档」）
  python pack_models.py --mirror-base https://xxx.r2.dev/models-v1/   # 额外追加国内镜像直链

【可复现归档（--manifest-only 的立身之本）】
manifest 里的 sha256/bytes 描述的是 **.tar.gz 归档本身**（models_bootstrap 下载后就按它校验），
所以不生成归档就无从得知哈希——除非归档是确定性的。为此这里把所有会引入随机性的东西全部钉死：
  · gzip 头的 mtime=0、filename=""（默认会写入「当前时间」和「原文件名」，两次打包哈希必不同 ← 踩过的坑）
  · gzip 压缩级别固定 GZIP_LEVEL（级别变了压缩流就变）
  · tar 头里的 mtime/uid/gid/uname/gname/mode 全部归一（否则换台机器、动一下文件时间戳哈希就变）
  · 成员按 KEEP 的固定顺序写入
于是「流式算哈希」与「真打包」产出的字节完全一致，--manifest-only 算出的 sha256 就是日后
真打出来那个 .tar.gz 的 sha256。改动上述任一常量都会让旧清单失效，必须重跑本脚本。
"""
import sys, os, json, gzip, hashlib, tarfile, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

# 运行时必需（其余如 model.onnx / model.onnx_data 是 fp32，仅重新量化才用）
KEEP = ("model_quantized.onnx", "config.json", "ort_config.json",
        "sentencepiece.bpe.model", "special_tokens_map.json",
        "tokenizer.json", "tokenizer_config.json")
NAMES = ("bge-m3-onnx", "bge-reranker-v2-m3-onnx")

# 下载直链的来源（GitHub Release）。集中成常量，别把 owner/repo 散在字符串里——
# 改仓库名时只改这三行，且 build_bundle 的占位符校验（url 含 "<" 即报错）不会被绕过。
GH_OWNER = "DrinkTea905"
GH_REPO = "paper-piggy"
MODELS_TAG = "models-v1"          # 模型资产挂在这个 Release tag 下

# 压缩级别：onnx INT8 权重本就近似随机，级别 9 相比 6 几乎压不动却慢好几倍，取 6。
# ⚠ 此值参与「可复现归档」契约，改它=旧 models_manifest.json 里的 sha256 全部作废。
GZIP_LEVEL = 6


def gh_url(filename):
    """该资产在 GitHub Release 上的下载直链（主线路）。"""
    return (f"https://github.com/{GH_OWNER}/{GH_REPO}/releases/"
            f"download/{MODELS_TAG}/{filename}")


class _HashSink:
    """只算 sha256、只数字节、不落盘的伪文件对象（--manifest-only 用）。
    GzipFile 往 fileobj 上只会调用 write()/flush()，实现这两个即够。"""

    def __init__(self):
        self.h = hashlib.sha256()
        self.n = 0

    def write(self, b):
        self.h.update(b)
        self.n += len(b)
        return len(b)

    def flush(self):
        pass


def _norm(ti):
    """归一化 tar 成员头：抹掉 mtime/属主/权限等环境相关字段，保证归档可复现。"""
    ti.mtime = 0
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o644
    return ti


def _write_archive(model_dir, name, fileobj):
    """把 model_dir 下 KEEP 里的文件写成 tar.gz 字节流到 fileobj（真文件或 _HashSink）。
    归档顶层即 <name>/，解压到 MODELS 目录即就位（models_bootstrap 直接 extractall 到 C.MODELS）。"""
    # 不用 tarfile.open(mode="w:gz")：它内部建的 GzipFile 会写入当前时间戳和原文件名，破坏可复现性。
    gz = gzip.GzipFile(filename="", mode="wb", compresslevel=GZIP_LEVEL,
                       fileobj=fileobj, mtime=0)
    try:
        # GNU_FORMAT：避免 PAX 扩展头把浮点 mtime 之类的东西写进去引入抖动
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
            for fn in KEEP:                      # 固定顺序 → 归档字节固定
                p = model_dir / fn
                if p.exists():
                    tf.add(p, arcname=f"{name}/{fn}", filter=_norm)
                else:
                    print(f"    ⚠ 缺 {fn}（可能该模型不需要）")
    finally:
        gz.close()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(C.MODELS), help="模型父目录（含 bge-m3-onnx 等）")
    ap.add_argument("--out", default=str(C.APP / "dist" / "model_assets"))
    ap.add_argument("--manifest-only", action="store_true",
                    help="只生成 models_manifest.json，不把 ~1.2GB 的 tar.gz 落盘"
                         "（哈希/大小靠可复现归档流式算出，与日后真打的包一致）")
    ap.add_argument("--mirror-base", default="",
                    help="国内镜像的目录前缀（如 Cloudflare R2 的 https://xxx.r2.dev/models-v1/），"
                         "会拼上文件名追加为第 2 条候选直链；不给则清单里只有 GitHub 一条")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    src = Path(args.src)
    out = Path(args.out)
    if not args.manifest_only:
        out.mkdir(parents=True, exist_ok=True)
    mirror_base = args.mirror_base.strip()
    if mirror_base and not mirror_base.endswith("/"):
        mirror_base += "/"

    models = []
    for name in NAMES:
        d = src / name
        if not (d / "model_quantized.onnx").exists():
            print(f"[pack] 跳过 {name}：无 model_quantized.onnx"); continue
        fname = f"{name}.tar.gz"
        tgz = out / fname
        if args.manifest_only and tgz.exists():
            # 归档已存在＝它就是将要上传的那个文件，直接哈希它（比重算更贴合事实）
            print(f"[pack] {name}：复用已有 {tgz}，算 sha256 …", flush=True)
            sz, digest = tgz.stat().st_size, sha256(tgz)
        elif args.manifest_only:
            print(f"[pack] {name} → 流式计算 {fname} 的 sha256/大小（不落盘）…", flush=True)
            sink = _HashSink()
            _write_archive(d, name, sink)
            sz, digest = sink.n, sink.h.hexdigest()
        else:
            print(f"[pack] 打包 {name} → {fname} …", flush=True)
            with open(tgz, "wb") as f:
                _write_archive(d, name, f)
            sz = tgz.stat().st_size
            print(f"    大小 {sz/1e6:.0f}MB，算 sha256 …", flush=True)
            digest = sha256(tgz)
        print(f"    {fname}  {sz/1e6:.0f}MB  sha256={digest[:16]}…", flush=True)

        # urls = 候选直链列表，models_bootstrap._download 按序尝试，任一成功即止。
        # ① GitHub Release（主）② 国内镜像（--mirror-base 给了才有；GitHub 在国内易超时，
        #    等 R2 之类的镜像就位后重跑本脚本带上 --mirror-base 即可，或直接手改本字段）。
        # ⚠ 千万别在这里塞 "<占位符>"：build_bundle.verify_manifest 见到 "<" 就 SystemExit。
        urls = [gh_url(fname)]
        if mirror_base:
            urls.append(mirror_base + fname)
        models.append({
            "name": name, "filename": fname, "bytes": sz,
            "sha256": digest,
            "urls": urls,
        })

    manifest = {
        "schema": 1,
        "repo": f"{GH_OWNER}/{GH_REPO}",
        "tag": MODELS_TAG,
        # 国内镜像预留位：填好镜像目录前缀后，重跑 `pack_models.py --manifest-only
        # --mirror-base <前缀>` 即可把镜像直链追加进各 models[].urls（此处仅作记录/提示）。
        "mirror_base": mirror_base,
        "gzip_level": GZIP_LEVEL,     # 记录压缩级别：日后校对哈希/复现归档时要对得上
        "note": ("models[].sha256/bytes 描述的是 .tar.gz 归档本身；把归档传到 "
                 f"https://github.com/{GH_OWNER}/{GH_REPO}/releases/tag/{MODELS_TAG} 即可。"
                 "urls 按序尝试，可追加国内镜像（见 mirror_base）。"),
        "models": models,
    }
    (C.APP / "models_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(m["bytes"] for m in models)
    print(f"[pack] 完成，共 {len(models)} 个资产，合计 {total/1e6:.0f}MB"
          + ("（清单-only，未落盘）" if args.manifest_only else f"，在 {out}"))
    # 显式校验：占位符 url 未替换会导致分发版首启下载必失败，构建前必须发现
    ph = [m["name"] for m in models
          if not m.get("urls") or any("<" in u for u in m["urls"])]
    if ph:
        print(f"[pack] ⚠ 以下模型的 url 仍是占位符（含 <…>），分发前务必替换为真实直链：{', '.join(ph)}")
    if args.manifest_only:
        print(f"[pack] ⚠ 归档未落盘。正式发布前请去掉 --manifest-only 真打一次，"
              f"并把 {out} 下的 .tar.gz 上传到 Release（tag={MODELS_TAG}）——"
              f"归档可复现，届时 sha256 应与本清单一致。")
    print(f"[pack] 清单写入 {C.APP/'models_manifest.json'}（build_bundle 会再校验一次 urls）")


if __name__ == "__main__":
    main()
