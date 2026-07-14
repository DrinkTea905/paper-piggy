# -*- coding: utf-8 -*-
"""
M4：导出 bge-reranker-v2-m3 → ONNX → INT8 动态量化，并验证与 torch fp32 的排序一致性 + 提速。
转换在开发机跑一次（需 optimum/torch/onnx）；产物 data/models/bge-reranker-v2-m3-onnx/ 随包分发，
运行时 reranker.py 用裸 onnxruntime 加载（不依赖 torch）。
用法: python setup_reranker_onnx.py
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ONNX_DIR = C.MODELS / "bge-reranker-v2-m3-onnx"
INT8 = "model_quantized.onnx"

def export_and_quantize():
    from transformers import AutoTokenizer
    from optimum.onnxruntime import ORTModelForSequenceClassification, ORTQuantizer
    from optimum.onnxruntime.configuration import AutoQuantizationConfig
    if not (ONNX_DIR / "model.onnx").exists():
        print("[onnx] 导出 reranker ONNX（首次，加载 fp32 约 2.3GB，稍候）...", flush=True)
        m = ORTModelForSequenceClassification.from_pretrained(C.RERANK_MODEL, export=True)
        m.save_pretrained(ONNX_DIR)
        AutoTokenizer.from_pretrained(C.RERANK_MODEL).save_pretrained(ONNX_DIR)
    if not (ONNX_DIR / INT8).exists():
        print("[onnx] INT8 动态量化 ...", flush=True)
        q = ORTQuantizer.from_pretrained(ONNX_DIR)
        cfg = AutoQuantizationConfig.avx2(is_static=False, per_channel=True)
        q.quantize(save_dir=ONNX_DIR, quantization_config=cfg)
    print(f"[onnx] 完成 -> {ONNX_DIR}", flush=True)

def main():
    export_and_quantize()
    from transformers import AutoTokenizer
    import onnxruntime as ort
    tok = AutoTokenizer.from_pretrained(ONNX_DIR)
    sess = ort.InferenceSession(str(ONNX_DIR / INT8), providers=["CPUExecutionProvider"])
    innames = {i.name for i in sess.get_inputs()}
    print("[onnx] 图输入:", innames)

    def onnx_scores(query, texts):
        inp = tok([[query, t] for t in texts], padding=True, truncation=True, max_length=512, return_tensors="np")
        feed = {k: v for k, v in inp.items() if k in innames}
        return sess.run(None, feed)[0].reshape(-1).tolist()

    # torch fp32 对照
    import torch
    from transformers import AutoModelForSequenceClassification
    tmdl = AutoModelForSequenceClassification.from_pretrained(C.RERANK_MODEL).eval()
    def torch_scores(query, texts):
        inp = tok([[query, t] for t in texts], padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            return tmdl(**inp).logits.view(-1).float().tolist()

    q = "认罪认罚从宽对司法信任的影响"
    docs = ["认罪认罚从宽制度的实证研究", "个人破产免责制度确立", "企业合规不起诉的适用",
            "未成年人社会观护体系", "今天天气很好适合散步"]
    o, t = onnx_scores(q, docs), torch_scores(q, docs)
    ro, rt = np.argsort(-np.array(o)).tolist(), np.argsort(-np.array(t)).tolist()
    print(f"\n=== 排序一致性 ===\n  onnx : {ro}\n  torch: {rt}\n  一致 : {ro == rt}")

    big = docs * 20  # 100 对
    for name, fn in [("onnx-int8", onnx_scores), ("torch-fp32", torch_scores)]:
        t0 = time.time()
        for _ in range(3):
            fn(q, big)
        print(f"  {name:11s} {(time.time()-t0)/3*1000:6.0f} ms / {len(big)} 对")

if __name__ == "__main__":
    main()
