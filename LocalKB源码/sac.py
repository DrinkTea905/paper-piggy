# -*- coding: utf-8 -*-
"""
自动 SAC（M2 文档摘要前缀）——用 LLM 给每篇文献生成 ~150 字中文摘要，作为嵌入前缀提升检索。
原来这步要人（对话里的 AI）跑；现在用用户配的 LLM API（默认 SiliconFlow 免费 DeepSeek）**全自动**。
- 存 data/summaries/summaries.json（{stem(safe_name): 摘要}），embed_index 会自动加载并拼进嵌入文本。
- 只对"缺摘要"的篇生成，幂等；每篇 1 次 LLM 调用（非每块）。
- settings.sac.enabled=False 或 key 空 → 跳过（退化为纯文本嵌入，不报错）。
"""
import sys, json, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import llm as L
import settings as S

SUM_FILE = C.DATA / "summaries" / "summaries.json"
SYS_PROMPT = ("你是学术文献摘要助手。用一段**约150字的中文**，概括这篇文献的核心主题、研究方法与主要结论，"
              "以便语义检索。只输出这段摘要本身，不要任何前缀、标题或解释。")


def _load():
    if SUM_FILE.exists():
        try:
            return json.loads(SUM_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(d):
    SUM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUM_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SUM_FILE)


def summarize_one(title, abstract, body, conf):
    src = f"标题：{title}\n"
    if abstract:
        src += f"原摘要：{abstract}\n"
    src += f"正文节选：{(body or '')[:1500]}"
    msgs = [{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": src}]
    return L.chat_once(msgs, conf.get("base"), conf.get("key"), conf.get("model"))


def _conf():
    """SAC 配置；key 为空时自动复用 API 后端的 base/key（用户配了 SiliconFlow 就一个 key 通吃）。"""
    c = dict(S.sac_conf())
    if not c.get("key"):
        a = S.api_conf()
        if a.get("key"):
            c["key"] = a.get("key")
            c["base"] = c.get("base") or a.get("base")
    return c


def enabled():
    c = _conf()
    return bool(c.get("enabled") and c.get("key"))


def ensure_for(items, log=print):
    """items: 可迭代 (stem, title, abstract, body)。给缺摘要者生成，写回 summaries.json。返回新增数。
    未启用或无 key 时直接返回 0（静默）。"""
    conf = _conf()
    if not (conf.get("enabled") and conf.get("key")):
        return 0
    sums = _load()
    n, fail = 0, 0
    for stem, title, abstract, body in items:
        if sums.get(stem, "").strip():
            continue
        try:
            s = summarize_one(title, abstract, body, conf)
            if s:
                sums[stem] = s
                n += 1
                if n % 5 == 0:
                    _save(sums)
                    log(f"[sac] 已生成 {n} 篇摘要 …")
        except Exception as e:
            fail += 1
            log(f"[sac] {stem} 生成失败：{e}")
            if fail >= 5:
                log("[sac] 连续失败过多，停止本轮（检查 key/网络/额度）")
                break
    _save(sums)
    if n:
        log(f"[sac] 本轮新增 {n} 篇摘要")
    return n
