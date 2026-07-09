# -*- coding: utf-8 -*-
"""
bge-reranker-v2-m3 重排。
优先 ONNX-INT8（裸 onnxruntime，不 import torch，CPU 上约 3.5x 快、且打包可砍 torch 526MB）；
若 ONNX 模型不存在则回退 torch 版（兼容未转换的环境）。M4。
转换：python setup_reranker_onnx.py（开发机一次）。
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

ONNX_DIR = C.MODELS / "bge-reranker-v2-m3-onnx"
INT8 = "model_quantized.onnx"

class Reranker:
    def __init__(self, model=C.RERANK_MODEL, max_length=512, batch_size=16):
        self.max_length = max_length
        self.bs = batch_size
        onnx_path = ONNX_DIR / INT8
        if onnx_path.exists():
            self.backend = "onnx"
            import onnxruntime as ort
            from transformers import AutoTokenizer
            self.tok = AutoTokenizer.from_pretrained(ONNX_DIR)
            so = ort.SessionOptions()
            so.intra_op_num_threads = 0  # 用满 CPU 核
            self.sess = ort.InferenceSession(str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"])
            self.innames = {i.name for i in self.sess.get_inputs()}
        else:
            self.backend = "torch"
            import torch
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            self._torch = torch
            self.tok = AutoTokenizer.from_pretrained(model)
            self.mdl = AutoModelForSequenceClassification.from_pretrained(model).eval()

    def scores(self, query, texts):
        """返回每个 text 对 query 的相关性分数（越高越相关）。"""
        out = []
        for i in range(0, len(texts), self.bs):
            batch = [[query, t] for t in texts[i:i + self.bs]]
            rt = "np" if self.backend == "onnx" else "pt"
            inp = self.tok(batch, padding=True, truncation=True, max_length=self.max_length, return_tensors=rt)
            if self.backend == "onnx":
                feed = {k: v for k, v in inp.items() if k in self.innames}
                logits = self.sess.run(None, feed)[0]
                out.extend(float(x) for x in logits.reshape(-1))
            else:
                with self._torch.no_grad():
                    out.extend(self.mdl(**inp).logits.view(-1).float().tolist())
        return out


class APIReranker:
    """通过 OpenAI 兼容的 /rerank API 打分（默认 SiliconFlow bge-reranker-v2-m3，免费）。
    与本地 Reranker 同接口（scores(query, texts)→list[float]，按原顺序）。"""
    def __init__(self, base, key, model="BAAI/bge-reranker-v2-m3", batch=64, timeout=60):
        import requests
        self._rq = requests
        self.base = base.rstrip("/"); self.key = key; self.model = model
        self.batch = batch; self.timeout = timeout

    def _post(self, query, docs):
        last = None
        for attempt in range(5):
            try:
                r = self._rq.post(self.base + "/rerank",
                                  headers={"Authorization": "Bearer " + self.key},
                                  json={"model": self.model, "query": query, "documents": docs,
                                        "return_documents": False},
                                  timeout=self.timeout)
            except Exception as e:                              # 网络层异常（连接/超时）：可重试
                last = repr(e); time.sleep(min(10, 1.5 ** attempt)); continue
            if r.status_code == 429:
                time.sleep(min(30, 2 ** attempt)); last = "429 限流"; continue
            if r.status_code in (500, 502, 503, 504):           # 服务端过载：退避重试
                last = f"{r.status_code} 服务端繁忙"; time.sleep(min(10, 1.5 ** attempt)); continue
            if 400 <= r.status_code < 500:                       # R1：4xx(密钥/额度/参数) 重试无用，快速失败
                raise RuntimeError(_client_err(r.status_code, self.model))
            try:                                                # 2xx：解析（偶发坏响应仍可重试）
                r.raise_for_status()
                sc = [0.0] * len(docs)
                for item in r.json().get("results", []):
                    idx = item.get("index", 0)
                    if 0 <= idx < len(docs):
                        sc[idx] = float(item.get("relevance_score", 0.0))
                return sc
            except Exception as e:
                last = repr(e); time.sleep(min(10, 1.5 ** attempt))
        raise RuntimeError(f"重排 API 连续失败：{last}（检查 API key / 网络 / 额度）")

    def scores(self, query, texts):
        out = []
        for i in range(0, len(texts), self.batch):
            out.extend(self._post(query, list(texts[i:i + self.batch])))
        return out


def _client_err(code, model=""):
    """R1：把 4xx 客户端错误翻成人话（重试无用，直接抛给用户看）。"""
    if code in (401, 403):
        return "密钥无效或余额不足，请检查 API Key 与余额"
    if code == 404:
        return f"接口地址或模型不存在（404，模型 {model}），请检查 base 地址与模型名"
    if code == 400:
        return f"请求被拒（400），请检查模型名（{model}）与参数"
    return f"重排 API 客户端错误（HTTP {code}），请检查 API Key / 模型 / base 地址"


def get_reranker(model=C.RERANK_MODEL, **kw):
    """工厂：按 settings.backend 返回本地 Reranker 或 APIReranker。"""
    import settings as S
    if S.is_api():
        a = S.api_conf()
        return APIReranker(a.get("base"), a.get("key", ""), a.get("rerank_model", "BAAI/bge-reranker-v2-m3"))
    return Reranker(model, **kw)
