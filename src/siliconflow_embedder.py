# -*- coding: utf-8 -*-
"""
SiliconFlow（及任意 OpenAI 兼容）/embeddings 嵌入调用封装。

设计目标
--------
- **drop-in**：与本地 `embedder.Embedder` 完全同接口——
  `encode(texts, batch_size=None, max_length=None) -> np.ndarray(float32, shape=(n, dim))`，逐行 L2 归一，
  空输入返回 `np.zeros((0, dim), np.float32)`；返回 numpy 数组（调用方依赖 `.shape` / `.tolist()`）。
- **位置构造兼容**：`SiliconFlowEmbedder(base, key, model)`，别名 `APIEmbedder`，
  兼容 server.py 里 `APIEmbedder(base, key, em).encode([...])` 的既有用法。
- **健壮**：连接复用、分批、429/5xx 指数退避（尊重 Retry-After）、空串保护、维度校验、清晰中文报错。

⚠️ 铁律：建库与查询必须用同一后端。本地 INT8 与 API 全精度向量不在同一空间，混用会掉点。
   API 向量默认做 L2 归一（与本地 ONNX 的 CLS+L2 一致），务必保持 normalize=True。

端点契约（SiliconFlow，OpenAI 兼容）
------------------------------------
POST {base}/embeddings   Header: Authorization: Bearer <key>
body: {"model": <id>, "input": <str|list[str]>, "encoding_format": "float"}
resp: {"data": [{"embedding": [...], "index": i}, ...], "usage": {...}}
可用模型：BAAI/bge-m3（免费，≤8192 token，dim=1024）、Pro/BAAI/bge-m3（付费高优先级，同空间）。
"""
import sys, time, random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as C
import numpy as np

# SiliconFlow 官方默认地址与免费模型
DEFAULT_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "BAAI/bge-m3"
# 单次请求最多塞多少条 input（护栏；超过则内部再细分，避免请求过大被拒/超时）
MAX_BATCH = 64


# 类型化异常：让批量嵌入的调用方能区分「重试无用、立即中止」与「传输抖动、可再等」。
# 都继承 RuntimeError —— 老代码里的 except RuntimeError / except Exception 全兼容，不破坏现有捕获。
class EmbedError(RuntimeError):
    pass


class EmbedClientError(EmbedError):
    """4xx（密钥无效 / 余额不足 / 模型名错 / base 地址错）——重试无用，整批立即中止。"""


class EmbedTransientError(EmbedError):
    """限流 / 服务端繁忙 / 网络超时 / 返回条数不符——重试耗尽后抛；连续多次才判死。"""


class SiliconFlowEmbedder:
    """OpenAI 兼容 /embeddings 客户端（默认 SiliconFlow，免费 bge-m3）。"""

    def __init__(self, base=DEFAULT_BASE, key="", model=DEFAULT_MODEL, *,
                 dim=None, batch_size=32, timeout=60, max_retries=5, normalize=True):
        import requests
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self.key = key or ""
        self.model = model or DEFAULT_MODEL
        self.dim = int(dim) if dim else getattr(C, "EMBED_DIM", 1024)
        self.batch_size = max(1, min(int(batch_size or 32), MAX_BATCH))
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.normalize = bool(normalize)
        # 复用连接：建库要发成千上万条，Session 省掉每次 TLS 握手
        self._sess = requests.Session()
        self._sess.headers.update({
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json",
        })

    # ---- 工厂：从用户设置构造（settings.json 的 api 段）----
    @classmethod
    def from_settings(cls, **kw):
        import settings as S
        a = S.api_conf()
        return cls(a.get("base", DEFAULT_BASE), a.get("key", ""),
                   a.get("embed_model", DEFAULT_MODEL), **kw)

    # ---- 单批请求（含退避重试）----
    def _post(self, batch):
        url = self.base + "/embeddings"
        payload = {"model": self.model, "input": batch, "encoding_format": "float"}
        last = None
        for attempt in range(self.max_retries):
            try:
                r = self._sess.post(url, json=payload, timeout=self.timeout)
            except Exception as e:                              # 网络层异常（连接/超时）：可重试
                last = repr(e)
                time.sleep(min(10, 1.5 ** attempt) + random.uniform(0, 0.3)); continue
            if r.status_code == 429:                            # 限流：优先听 Retry-After
                wait = _retry_after(r) or min(30, 2 ** attempt)
                last = "429 限流"; time.sleep(wait + random.uniform(0, 0.3)); continue
            if r.status_code in (500, 502, 503, 504):           # 服务端过载：退避重试
                last = f"{r.status_code} 服务端繁忙"
                time.sleep(min(10, 1.5 ** attempt) + random.uniform(0, 0.3)); continue
            if 400 <= r.status_code < 500:                       # R1：4xx(密钥/额度/参数/模型名) 重试无用，快速失败
                raise EmbedClientError(_client_err(r.status_code, self.model))
            try:                                                # 2xx：解析（偶发坏响应/维度不符仍可重试）
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda x: x.get("index", 0))
                v = np.asarray([d["embedding"] for d in data], dtype=np.float32)
                if v.shape[0] != len(batch):
                    raise RuntimeError(f"返回条数 {v.shape[0]} 与请求 {len(batch)} 不符")
                if v.shape[1] != self.dim:
                    raise RuntimeError(
                        f"返回维度 {v.shape[1]} ≠ 期望 {self.dim}（模型 {self.model} 配错？"
                        f"建库与查询须同模型/同维度）")
                if self.normalize:
                    v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)
                return v.astype(np.float32)
            except Exception as e:
                last = repr(e)
                time.sleep(min(10, 1.5 ** attempt) + random.uniform(0, 0.3))
        raise EmbedTransientError(f"嵌入 API 连续失败：{last}（检查 API key / 网络 / 额度 / base 地址）")

    # ---- 对外主接口（与本地 Embedder 同签名）----
    def encode(self, texts, batch_size=None, max_length=None):
        """texts: list[str] -> np.ndarray(float32, (n, dim))，逐行 L2 归一。
        max_length 仅为签名兼容（服务端自行截断，不发给 API）。"""
        if texts is None:
            return np.zeros((0, self.dim), np.float32)
        if isinstance(texts, str):
            texts = [texts]
        n = len(texts)
        if n == 0:
            return np.zeros((0, self.dim), np.float32)
        bs = max(1, min(int(batch_size or self.batch_size), MAX_BATCH))
        out = []
        for i in range(0, n, bs):
            # 空串/纯空白保护：某些服务端对空 input 报错，替换成单空格占位
            chunk = [(t if (t and str(t).strip()) else " ") for t in texts[i:i + bs]]
            out.append(self._post(chunk))
        return np.concatenate(out) if out else np.zeros((0, self.dim), np.float32)

    # 便捷：直接拿单条向量
    def encode_one(self, text, max_length=None):
        return self.encode([text], max_length=max_length)[0]


def _client_err(code, model=""):
    """R1：把 4xx 客户端错误翻成人话（重试无用，直接抛给用户看）。"""
    if code in (401, 403):
        return "密钥无效或余额不足，请检查 API Key 与余额"
    if code == 404:
        return f"接口地址或模型不存在（404，模型 {model}），请检查 base 地址与模型名"
    if code == 400:
        return f"请求被拒（400），请检查模型名（{model}）与参数"
    return f"嵌入 API 客户端错误（HTTP {code}），请检查 API Key / 模型 / base 地址"


def _retry_after(resp):
    """解析 Retry-After 头（秒）。无/非法则返回 None。"""
    try:
        ra = resp.headers.get("Retry-After")
        return float(ra) if ra else None
    except Exception:
        return None


# 兼容别名：老代码里 `APIEmbedder(base, key, model)` 继续可用
APIEmbedder = SiliconFlowEmbedder


# 模块级便捷函数（临时/脚本用；建库/检索走 embedder.get_embedder）
def embed_texts(texts, base=DEFAULT_BASE, key="", model=DEFAULT_MODEL, **kw):
    return SiliconFlowEmbedder(base, key, model, **kw).encode(texts)


if __name__ == "__main__":
    # 自测：python siliconflow_embedder.py <API_KEY>
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    k = sys.argv[1] if len(sys.argv) > 1 else ""
    if not k:
        try:
            import settings as S
            k = S.api_conf().get("key", "")
        except Exception:
            pass
    if not k:
        print("用法: python siliconflow_embedder.py <SiliconFlow_API_KEY>（或在 settings.json 里填 api.key）")
        sys.exit(1)
    emb = SiliconFlowEmbedder(key=k)
    v = emb.encode(["法律", "行政处罚的比例原则", ""])
    print(f"模型={emb.model}  形状={v.shape}  dtype={v.dtype}")
    print(f"每行范数≈1？ {np.round(np.linalg.norm(v, axis=1), 4).tolist()}")
    print(f"cos(法律, 行政处罚)= {float(v[0] @ v[1]):.4f}")
