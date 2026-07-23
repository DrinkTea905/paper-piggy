# -*- coding: utf-8 -*-
"""
自动 SAC（M2 文档摘要前缀）——用 LLM 给每篇文献生成 ~150 字中文摘要，作为嵌入前缀提升检索。
PaperPiggy 自动生成可复用检索引擎的 SiliconFlow Key + 免费简单模型，也可使用用户另选的文本生成厂商。
- 存 data/summaries/summaries.json（{stem(safe_name): 摘要}），embed_index 会自动加载并拼进嵌入文本。
- 只对"缺摘要"的篇生成，幂等；每篇 1 次 LLM 调用（非每块）。
- 未选择 PaperPiggy 自动生成，或所选来源没有可用 Key → 跳过（不影响深索与正文检索）。
"""
import sys, json, os, re, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import llm as L
import settings as S

SUM_FILE = C.DATA / "summaries" / "summaries.json"
SYS_PROMPT = ("你是学术文献摘要助手。用一段**约150字的中文**，概括这篇文献的核心主题、研究方法与主要结论，"
              "以便语义检索。只输出这段摘要本身，不要任何前缀、标题或解释。")
MIN_SUMMARY_CHARS = 60
MAX_SUMMARY_CHARS = 500
_NO_BODY_MARKERS = ("无摘要和正文", "没有摘要和正文", "无正文内容", "未提供正文")


def _load():
    if SUM_FILE.exists():
        try:
            data = json.loads(SUM_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _save(d):
    SUM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUM_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SUM_FILE)


def validate_summary(summary):
    """返回 ``(是否合格, 原因)``。只挡住明显损坏或已经过期的检索摘要。

    阈值刻意宽松：简洁但有信息的摘要可以通过；问号乱码、无限重复、失控长文，
    以及声称“没有正文”的旧占位摘要不能再冒充已完成。
    """
    if not isinstance(summary, str):
        return False, "摘要格式错误（应为文本）"
    s = summary.strip()
    if not s:
        return False, "摘要为空"
    if len(s) < MIN_SUMMARY_CHARS:
        return False, f"摘要过短（{len(s)} 字）"
    if len(s) > MAX_SUMMARY_CHARS:
        return False, f"摘要过长（{len(s)} 字，疑似生成失控）"
    if "\ufffd" in s:
        return False, "含有损坏字符"
    if any(marker in s for marker in _NO_BODY_MARKERS):
        return False, "摘要仍声称没有正文，可能早于 OCR 结果"

    visible = [ch for ch in s if not ch.isspace()]
    useful = sum(ch.isalnum() or "\u3400" <= ch <= "\u9fff" for ch in visible)
    question_marks = sum(ch in "?？" for ch in visible)
    if useful < 30 or (visible and question_marks / len(visible) > 0.20):
        return False, "有效文字过少，疑似乱码"

    punctuation_run = re.search(r"[，,。；;：:！？!?、]{3,}", s)
    if punctuation_run:
        return False, f"标点“{punctuation_run.group(0)}”异常连续，疑似生成损坏"

    # 保守抓取相邻重复的中文短语：重要性重要性 / 测量方法，测量方法 / 研究设计，设计了。
    # 只允许空白或标点隔开，正常的“问卷设计、访谈设计”不会命中。
    chinese_repeat = re.search(r"([\u3400-\u9fff]{2,8})[\s，,、；;：:]*\1", s)
    if chinese_repeat:
        return False, f"中文短语“{chinese_repeat.group(1)}”连续重复，疑似生成损坏"

    words = re.findall(r"[A-Za-z]+|\d+", s.lower())
    run = 1
    for idx in range(1, len(words)):
        run = run + 1 if words[idx] == words[idx - 1] else 1
        if run >= 8:
            return False, f"词语“{words[idx]}”连续重复，疑似生成失控"
    return True, ""


def audit(summaries=None):
    """审计摘要库；不会改文件。返回有效键、异常原因及总数。"""
    sums = _load() if summaries is None else summaries
    valid, invalid = set(), {}
    for key, summary in sums.items():
        ok, reason = validate_summary(summary)
        if ok:
            valid.add(key)
        else:
            invalid[key] = reason
    return {"valid": valid, "invalid": invalid, "total": len(sums)}


def load_valid():
    """只返回通过质量闸门的摘要，供嵌入前缀使用。"""
    sums = _load()
    return {key: value for key, value in sums.items() if validate_summary(value)[0]}


def summarize_one(title, abstract, body, conf):
    src = f"标题：{title}\n"
    if abstract:
        src += f"原摘要：{abstract}\n"
    src += f"正文节选：{(body or '')[:1500]}"
    msgs = [{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": src}]
    return L.chat_once(msgs, conf.get("base"), conf.get("key"), conf.get("model"), max_tokens=320)


def _conf():
    """返回当前自动摘要的有效配置；复用和独立厂商是明确的两条路径。"""
    c = dict(S.sac_conf())
    if c.get("source") == "reuse":
        a = S.api_conf()
        # “复用”只承诺复用 SiliconFlow：普通对话 API 即使 OpenAI 兼容，也未必有检索所需模型，
        # 更不能把别家 Key 发往 SiliconFlow。旧配置若把检索 Base 改成别家，这里明确判为未就绪。
        if "siliconflow" in (a.get("base") or "").lower():
            c["base"] = a.get("base") or "https://api.siliconflow.cn/v1"
            c["key"] = a.get("key") or ""
            c["model"] = S.DEFAULT["sac"]["model"]
        else:
            c["key"] = ""
    return c


def enabled():
    """K2：仅当 generator=="server"（服务端用 API Key 自动生成）且有 key 时，服务端才生成摘要。
       generator=="agent" 时服务端不生成——摘要由 Agent 经 /index/deep_agent 写进 summaries.json；
       generator=="off" 也不生成。这样 deep_embed 里的服务端 SAC 会被跳过，只认已有摘要。"""
    c = _conf()
    return bool(c.get("generator") == "server" and c.get("key"))


def write_summaries(items):
    """#7：把 Agent 写好的检索摘要合并进 summaries.json（键用 safe_name(stem)，与 embed_index 一致）。
       items：可迭代 {"key":..,"summary":..} 或 (key, summary)。幂等 merge、原子写。
       整批先校验：只要有一篇异常就一篇不写，避免后续把坏摘要标成“深索完成”。"""
    import textutil as T
    prepared, errors = [], []
    for it in (items or []):
        if isinstance(it, dict):
            key = it.get("key"); summ = (it.get("summary") or "").strip()
        else:
            key, summ = it[0], (it[1] or "").strip()
        if not key:
            errors.append({"key": "", "reason": "缺少文献 key"})
            continue
        safe_key = T.safe_name(key)
        ok, reason = validate_summary(summ)
        if not ok:
            errors.append({"key": key, "reason": reason})
        else:
            prepared.append((safe_key, summ))
    if errors:
        return {"written": 0, "accepted_keys": [], "errors": errors}
    sums = _load()
    for key, summ in prepared:
        sums[key] = summ
    if prepared:
        _save(sums)
    return {"written": len(prepared), "accepted_keys": [k for k, _ in prepared], "errors": []}


def snapshot(keys):
    """保存指定摘要写前状态，供 Agent 摘要重嵌入失败时恢复。"""
    import textutil as T
    sums = _load()
    return {T.safe_name(k): sums.get(T.safe_name(k)) for k in (keys or []) if k}


def restore(snap):
    """恢复 ``snapshot`` 生成的局部快照；None 表示写前不存在。"""
    sums = _load()
    for key, value in (snap or {}).items():
        if value is None:
            sums.pop(key, None)
        else:
            sums[key] = value
    _save(sums)


def summary_keys():
    """通过质量闸门的检索摘要 stem 集合（键同 embedded_keys.txt 的 safe_name(stem)）。
       供 server 统计「已深索里多少篇有摘要」= deep ∩ summary。"""
    return audit()["valid"]


def summary_issues():
    """返回 {stem: 异常原因}；只读，不会自动重生成。"""
    return audit()["invalid"]


def get(stem):
    """按 safe_name(stem) 取该篇检索摘要文本；无则空串。供「点开查看摘要」只读展示。"""
    return _load().get(stem, "") or ""


def inspect(stem):
    """取得摘要文本及质量状态，供 API/UI 把异常与缺失区分开。"""
    summary = get(stem)
    ok, reason = validate_summary(summary)
    return {"summary": summary if ok else "", "valid": ok, "reason": reason if summary else ""}


def key_available():
    """补生成是否有可用的 API key（复用 SiliconFlow，或独立 AI 厂商配置）。"""
    return bool(_conf().get("key"))


def gen_missing(items, log=print, on_progress=None):
    """补生成：给 items 里「缺摘要」的篇用 LLM 生成并写回，**不受 generator 门控**
       （用户显式点「补生成摘要」时无论 off/agent/server 都要生成）。
       items: 可迭代 (stem, title, abstract, body)。返回 (成功数, 失败数)。
       无 key 时直接返回 (0, 0)（调用方应先用 key_available() 拦截并提示）。"""
    conf = _conf()
    if not conf.get("key"):
        return 0, 0
    sums = _load()
    n, fail, run_fail = 0, 0, 0
    for stem, title, abstract, body in items:
        if validate_summary(sums.get(stem, ""))[0]:
            continue
        try:
            s = summarize_one(title, abstract, body, conf)
            ok, reason = validate_summary(s)
            if ok:
                sums[stem] = s
                n += 1
                run_fail = 0
                if n % 3 == 0:
                    _save(sums)
                if on_progress:
                    on_progress(n, fail)
            else:
                fail += 1; run_fail += 1
                log(f"[sac] {stem} 生成结果未通过质量检查：{reason}")
                if run_fail >= 5:
                    log("[sac] 连续生成异常摘要，停止本轮补生成（请检查模型与提示词）")
                    break
        except Exception as e:
            fail += 1; run_fail += 1
            log(f"[sac] {stem} 生成失败：{e}")
            if run_fail >= 5:
                log("[sac] 连续失败过多，停止本轮补生成（检查 key/网络/额度）")
                break
    _save(sums)
    if n:
        log(f"[sac] 补生成完成，新增 {n} 篇摘要（失败 {fail}）")
    return n, fail


def ensure_for(items, log=print):
    """items: 可迭代 (stem, title, abstract, body)。给缺摘要者生成，写回 summaries.json。返回新增数。
    未启用（generator!=server）或无 key 时直接返回 0（静默）。"""
    conf = _conf()
    if not (conf.get("generator") == "server" and conf.get("key")):
        return 0
    sums = _load()
    n, fail = 0, 0
    for stem, title, abstract, body in items:
        if validate_summary(sums.get(stem, ""))[0]:
            continue
        try:
            s = summarize_one(title, abstract, body, conf)
            ok, reason = validate_summary(s)
            if ok:
                sums[stem] = s
                n += 1
                if n % 5 == 0:
                    _save(sums)
                    log(f"[sac] 已生成 {n} 篇摘要 …")
            else:
                fail += 1
                log(f"[sac] {stem} 生成结果未通过质量检查：{reason}")
                if fail >= 5:
                    log("[sac] 生成异常摘要过多，停止本轮（请检查模型与提示词）")
                    break
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
