# -*- coding: utf-8 -*-
"""bge-m3 dense 嵌入器（ONNX + INT8，CPU 上约 3.5x fp32；与 fp32 向量 cosine≈0.986、排序一致）。
建库(03)与查询(daemon)共用本模块，保证向量空间一致。
若 ONNX 模型不存在，先运行: python setup_onnx.py
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import numpy as np

ONNX_DIR = C.MODELS / "bge-m3-onnx"
INT8_FILE = "model_quantized.onnx"

class Embedder:
    def __init__(self, max_length=512, batch_size=32):
        # 裸 onnxruntime 加载（不经 optimum，故运行期不 import torch → 打包可砍 torch ~500MB）。
        # 已验证与 optimum.ORTModelForFeatureExtraction 输出向量 cosine=1.000000（同一 quantized.onnx）。
        import onnxruntime as ort
        from transformers import AutoTokenizer
        # Use the local tokenizer so rebuilds never depend on HuggingFace network access.
        self.tok = AutoTokenizer.from_pretrained(ONNX_DIR, local_files_only=True)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 0  # 用满 CPU 核
        self.sess = ort.InferenceSession(str(ONNX_DIR / INT8_FILE), sess_options=so,
                                         providers=["CPUExecutionProvider"])
        self.innames = {i.name for i in self.sess.get_inputs()}  # bge-m3: input_ids, attention_mask
        self.max_length = max_length
        self.batch_size = batch_size

    def encode(self, texts, batch_size=None, max_length=None):
        """返回 (n,1024) 的 L2 归一化 dense 向量（np.float32）。"""
        bs = batch_size or self.batch_size
        ml = max_length or self.max_length
        out = []
        for i in range(0, len(texts), bs):
            b = texts[i:i + bs]
            inp = self.tok(b, padding=True, truncation=True, max_length=ml, return_tensors="np")
            feed = {k: v for k, v in inp.items() if k in self.innames}
            lhs = self.sess.run(None, feed)[0]                   # last_hidden_state (n, seq, 1024)
            cls = np.asarray(lhs)[:, 0]                          # CLS pooling
            cls = cls / (np.linalg.norm(cls, axis=1, keepdims=True) + 1e-12)
            out.append(cls.astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, C.EMBED_DIM), np.float32)


# API 嵌入器已抽到独立模块 siliconflow_embedder.py（封装 429/5xx 指数退避、Retry-After、
# 维度校验、连接复用、空串保护）。此处重导出 APIEmbedder 别名，保持全库零改动 drop-in。
from siliconflow_embedder import SiliconFlowEmbedder, APIEmbedder   # noqa: F401,E402


def get_embedder(**kw):
    """工厂：按 settings.backend 返回本地 Embedder 或 APIEmbedder。"""
    import settings as S
    if S.is_api():
        a = S.api_conf()
        return APIEmbedder(a.get("base"), a.get("key", ""), a.get("embed_model", "BAAI/bge-m3"))
    return Embedder(**kw)
