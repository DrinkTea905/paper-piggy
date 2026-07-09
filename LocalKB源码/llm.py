# -*- coding: utf-8 -*-
"""OpenAI 兼容 chat（用户自备 API key），流式输出。内置常用服务商预设。
只用 requests，无额外依赖。key 只在本机内存/本地设置里，不外传到任何第三方（除用户选的模型服务商）。
"""
import json
import requests

# provider -> {base_url, 默认模型, 获取key的控制台链接}
# 硅基流动默认用免费的 Qwen/Qwen3-8B（131K 上下文、中文强）；其它免费可选见 web 里的提示。
PROVIDERS = {
    "deepseek":    {"base": "https://api.deepseek.com/v1",          "model": "deepseek-chat",     "keyurl": "https://platform.deepseek.com/api_keys"},
    "siliconflow": {"base": "https://api.siliconflow.cn/v1",        "model": "Qwen/Qwen3-8B",     "keyurl": "https://cloud.siliconflow.cn/account/ak"},
    "kimi":        {"base": "https://api.moonshot.cn/v1",           "model": "moonshot-v1-32k",   "keyurl": "https://platform.moonshot.cn/console/api-keys"},
    "zhipu":       {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus",        "keyurl": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"},
    "openai":      {"base": "https://api.openai.com/v1",            "model": "gpt-4o",            "keyurl": "https://platform.openai.com/api-keys"},
    "custom":      {"base": "",                                     "model": "",                  "keyurl": ""},
}

def resolve(provider, base_url, model):
    """把 provider 预设与用户覆盖合并成最终 (base_url, model)。"""
    p = PROVIDERS.get(provider, {})
    return (base_url or p.get("base", "")).rstrip("/"), (model or p.get("model", ""))

def _friendly_error(e):
    """把 requests 的底层异常翻成人话 RuntimeError（C5）。
    server.py 的 chat 用 str(e) 透传、wiki 用 detail 展示——抛这个后用户看到的就是人话。"""
    code = getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(e, requests.exceptions.HTTPError):
        if code in (401, 403):
            return RuntimeError("密钥无效或余额不足，请检查 API Key 与余额")
        if code == 429:
            return RuntimeError("请求太频繁，请稍后再试")
        return RuntimeError(f"模型服务返回错误（HTTP {code}），请稍后再试")
    if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return RuntimeError("网络连接失败，请检查网络")
    return RuntimeError(str(e) or "调用模型失败，请稍后再试")

def _payload(model, messages, temperature, stream):
    """构造请求体。Qwen3 是混合推理模型，默认会吐 <think> 思维链——聊天/摘要都不想要，
    对硅基流动关掉它（enable_thinking=false）；其它模型忽略该字段（OpenAI 兼容会丢弃未知键）。"""
    body = {"model": model, "messages": messages, "temperature": temperature, "stream": stream}
    if "qwen3" in (model or "").lower():
        body["enable_thinking"] = False
    return body

def chat_once(messages, base_url, api_key, model, temperature=0.3, timeout=120):
    """非流式：返回完整回复文本（供 SAC 摘要等后台批量调用）。"""
    if not base_url or not api_key or not model:
        raise ValueError("尚未配置 LLM（base/key/model）")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = _payload(model, messages, temperature, False)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:   # C5：网络/HTTP 错误翻成人话
        raise _friendly_error(e)
    return ((r.json().get("choices") or [{}])[0].get("message", {}) or {}).get("content", "").strip()

def chat_stream(messages, base_url, api_key, model, temperature=0.3):
    """生成器：逐段 yield 文本增量（OpenAI 兼容 /chat/completions, stream=true）。"""
    if not base_url or not api_key or not model:
        raise ValueError("尚未配置：请在右上「设置」里填服务商、API Key、模型名。")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = _payload(model, messages, temperature, True)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=180) as r:
            r.raise_for_status()
            # #6 对话乱码根因：SSE 响应头常不带 charset，requests 的 iter_lines(decode_unicode=True)
            # 会退回 ISO-8859-1(Latin-1) 解码 → UTF-8 中文被烤成「ä½ å¥½」乱码。强制按 UTF-8 解码。
            r.encoding = "utf-8"
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    j = json.loads(data)
                    delta = (j.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    continue
    except requests.exceptions.RequestException as e:   # C5：网络/HTTP 错误翻成人话
        raise _friendly_error(e)
