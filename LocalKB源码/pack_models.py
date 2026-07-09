# -*- coding: utf-8 -*-
"""
打包分发用的「瘦模型」资产（开发机跑一次）。
每个模型只保留运行时需要的文件（INT8 量化 onnx + tokenizer），排除 fp32 的 model.onnx_data(2.27GB)。
产出 dist/model_assets/<name>.tar.gz（各 ~590MB）+ models_manifest.json（含 sha256/大小/占位 URL）。
之后：把两个 .tar.gz 传到 GitHub Release，把下载直链填回 models_manifest.json 的 url 字段，
      再把 models_manifest.json 随分发包一起发（models_bootstrap.py 首启据此下载）。
用法: python pack_models.py [--src <模型父目录>] [--out <输出目录>]
"""
import sys, os, json, hashlib, tarfile, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

# 运行时必需（其余如 model.onnx / model.onnx_data 是 fp32，仅重新量化才用）
KEEP = ("model_quantized.onnx", "config.json", "ort_config.json",
        "sentencepiece.bpe.model", "special_tokens_map.json",
        "tokenizer.json", "tokenizer_config.json")
NAMES = ("bge-m3-onnx", "bge-reranker-v2-m3-onnx")


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
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    src = Path(args.src); out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    models = []
    for name in NAMES:
        d = src / name
        if not (d / "model_quantized.onnx").exists():
            print(f"[pack] 跳过 {name}：无 model_quantized.onnx"); continue
        tgz = out / f"{name}.tar.gz"
        print(f"[pack] 打包 {name} → {tgz.name} …", flush=True)
        with tarfile.open(tgz, "w:gz") as tf:
            for fn in KEEP:
                p = d / fn
                if p.exists():
                    tf.add(p, arcname=f"{name}/{fn}")   # 顶层即 <name>/，解压到 MODELS 即就位
                else:
                    print(f"    ⚠ 缺 {fn}（可能该模型不需要）")
        sz = tgz.stat().st_size
        print(f"    大小 {sz/1e6:.0f}MB，算 sha256 …", flush=True)
        placeholder = f"https://github.com/<用户名>/LocalKB/releases/download/models-v1/{tgz.name}"
        models.append({
            "name": name, "filename": tgz.name, "bytes": sz,
            "sha256": sha256(tgz),
            # urls 支持多镜像（按序尝试，国内 GitHub 直连易失败可加镜像）；占位符 <…> 必须替换
            "urls": [placeholder],
        })
    manifest = {"schema": 1,
                "note": "上传 .tar.gz 到 GitHub Release（可加镜像）后，把真实直链填入各 models[].urls",
                "models": models}
    (C.APP / "models_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pack] 完成，共 {len(models)} 个资产在 {out}")
    # 显式校验：占位符 url 未替换会导致分发版首启下载必失败，构建前必须发现
    ph = [m["name"] for m in models
          if not m.get("urls") or any("<" in u for u in m["urls"])]
    if ph:
        print(f"[pack] ⚠ 以下模型的 url 仍是占位符（含 <…>），分发前务必替换为真实直链：{', '.join(ph)}")
    print(f"[pack] 清单写入 {C.APP/'models_manifest.json'}（填好 urls 后再分发；build_bundle 会再校验一次）")


if __name__ == "__main__":
    main()
