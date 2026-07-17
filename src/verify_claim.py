# -*- coding: utf-8 -*-
"""
EN-A4（契约7，G1 核验器）：把一句论断（claim）核到库内证据上，三态判定。

保守原则（蓝图铁律三：**库内无 ≠ 论断为假**——这是整个核验器的宪法）：
  - supported：reranker 分显著（本地 logit>2 / API>0.6，读 settings.is_api() 分流）
    **且**在命中 chunk 里找得到与 claim 归一化重叠度足够高的支撑句（quote 非空）；
  - mismatch：**只留给硬证据**——claim 里带直接引文（引号段），该引文经 textloc 在库内
    定位到了原文，但落点不在调用方声称的 keys 里（引用错篇/疑似转引）。
    「支撑句与 claim 语义相左」这类软冲突机器判不动：语义冲突的判断错误代价极高
    （把对的说成错的 = 替人宣判），所以一律 not_in_lib 而不是 mismatch；
  - 其余一律 not_in_lib。note 里固定附一句「本库未覆盖不代表论断错误，请人工复核」。

证据链：① R.search(claim) 召回（full 模式带 reranker 分；剔除 wiki 行防自证）；
② 命中 chunk 内取与 claim 归一化 2-gram 重叠度最高的句子做 quote；
③ top 命中的 quote 再用 textloc 回原文精确定位页位（chunk 的 page 是切块页，
   支撑句可能落在 parent 上下文的相邻页——引注要落到页，以 locate 的落点为准）。
只读，不写任何文件。
"""
import sys, re, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import textloc as TL

# 铁律三的固定尾注：无论判成什么，都要提醒"未覆盖≠为假"
FIXED_NOTE = "本库未覆盖不代表论断错误，请人工复核。"

_SENT_SPLIT = re.compile(r"[。！？!?；;\n]")
# claim 里的直接引文段（引号包裹、8~120 字）：mismatch 硬证据通道的入口
_RE_QUOTED = re.compile(r'[“「『"]([^”」』"]{8,120})[”」』"]')

# 显著性阈值：本地 reranker 输出裸 logit（尺度 0~10+，可为负）；API（SiliconFlow）输出 0~1。
_LOCAL_THR, _API_THR = 2.0, 0.6
# 支撑句与 claim 的归一化 2-gram 重叠率下限（按较短方归一）——低于它宁可给空 quote，
# 也不硬凑一句"看着像"的话误导人。
_OVERLAP_THR = 0.30


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _best_sentence(claim_grams, text):
    """在一段 chunk 文本里找与 claim 归一化重叠度最高的句子。返回 (句子, 重叠率)。"""
    best, best_r = "", 0.0
    for s in _SENT_SPLIT.split(text or ""):
        s = s.strip()
        if len(s) < 8:                     # 太短的碎片当不了支撑句
            continue
        sg = _bigrams(TL.norm_text(s, fuzzy=True))
        if not sg:
            continue
        r = len(claim_grams & sg) / max(1, min(len(claim_grams), len(sg)))
        if r > best_r:
            best, best_r = s, r
    return best, best_r


def _norm_conf(score, api):
    """归一置信分：API 分本就在 0~1，钳位即可；本地 logit 过 sigmoid 压到 0~1。"""
    try:
        x = float(score or 0.0)
    except Exception:
        return 0.0
    if api:
        return max(0.0, min(1.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _mk_evidence(matches, seg, exact_score=1.0, fuzzy_score=0.9):
    """textloc 命中 → 契约7 的 evidence 条目（引文定位是硬证据，分数按 exact/fuzzy 定级）。"""
    return [{"key": m["key"], "pdf_page": m["pdf_page"], "printed_page": m["printed_page"],
             "quote": seg, "score": exact_score if m.get("exact") else fuzzy_score}
            for m in matches]


def _has_extracted_text(key):
    """声称出处是否已深索且有正文：读 extracted/<stem>.json，任一页 text 非空即算有。
       没正文时 textloc.locate 在该篇必然落空——那是"没得对"而非"对不上"，不能据此判 mismatch。"""
    try:
        st = TL._stem_of(key)
        if not st:
            return False
        d = TL._read_doc(C.EXTRACTED / f"{st}.json")
        if not d:
            return False
        return any((pg.get("text") or "").strip() for pg in (d.get("pages") or []))
    except Exception:
        return False


def verify(claim, keys=None, topk=8):
    """三态核验。返回契约7：{"verdict","confidence","evidence":[...],"note"}。"""
    import retriever as R
    import settings as S
    claim = (claim or "").strip()
    if not claim:
        raise ValueError("空 claim")
    topk = max(1, min(int(topk or 8), 20))
    api = S.is_api()
    if not R.STATE.get("ready"):
        return {"verdict": "not_in_lib", "confidence": 0.0, "evidence": [],
                "note": "索引尚未就绪，本次未能核验。" + FIXED_NOTE}

    # ── ① 硬证据通道：claim 带直接引文且声称了出处（keys）→ textloc 精确定位引文真身 ──
    # mismatch 只可能从这里产生：引文定位到了原文、但落点与声称出处对不上，这才是机器
    # 有资格断言的"硬对不上"；引文哪儿都找不到时绝不判 mismatch，落回下方常规通道。
    quoted = [m.group(1) for m in _RE_QUOTED.finditer(claim)]
    if quoted and keys:
        seg = max(quoted, key=len)         # 取最长引文段：越长越不可能撞车
        located = []
        for k in keys:
            try:
                r = TL.locate(seg, key=k, fuzzy=True, max_matches=3)
                located.extend(r.get("matches") or [])
            except Exception:
                pass                        # 单篇定位失败（太短/文件损坏）不拦路
        if located:
            ev = _mk_evidence(located[:3], seg)
            return {"verdict": "supported", "confidence": ev[0]["score"], "evidence": ev,
                    "note": "claim 中的直接引文已在声称出处定位到原文（含精确页位），可据 evidence 回原文复核。" + FIXED_NOTE}
        try:
            elsewhere = (TL.locate(seg, key=None, fuzzy=True, max_matches=3).get("matches") or [])
        except Exception:
            elsewhere = []
        if elsewhere:
            # 判 mismatch 前必须确认：声称出处里至少有一篇真的深索过且有正文——否则声称篇根本
            # 没正文可比，上面 locate 落空只是"没得对"而非"对不上"，据此改判他篇是冤案（对抗审查 #5）。
            if any(_has_extracted_text(k) for k in keys):
                ev = _mk_evidence(elsewhere[:3], seg)
                return {"verdict": "mismatch", "confidence": ev[0]["score"], "evidence": ev,
                        "note": f"claim 中的直接引文定位到了原文，但不在声称的出处（{'、'.join(list(keys)[:3])}）里"
                                "——疑似引用错篇或转引，请按 evidence 的实际落点核对。" + FIXED_NOTE}
            # 声称出处都没深索/无正文：无从核对，仅提示、不改判他篇（附他处落点供参考）
            ev = _mk_evidence(elsewhere[:3], seg)
            return {"verdict": "not_in_lib", "confidence": 0.0, "evidence": ev,
                    "note": "声称出处未深索/无正文，无法核对，仅供参考（引文在库内他处出现，见 evidence 落点）。" + FIXED_NOTE}
        # 引文库内无踪 → 不下判词，继续常规检索通道（铁律三：找不到 ≠ 引文是假的）

    # ── ② 常规通道：检索召回 + 支撑句 ──
    kset = set(keys) if keys else None
    try:
        # sort=relevance：核验只关心语义相关，不掺期刊层级加成（blend 的 tier bonus 会
        # 让高档期刊的弱相关块顶掉低档期刊的强支撑句，核验场景是反效果）
        # 已明确给出 keys 时属于定向核验：允许在选定文献内多取证据段；未给 keys 仍按
        # 发现型检索的每篇上限，避免一篇文献淹没整个候选集。
        hits = R.search(claim, topk, "relevance", keys=kset,
                        max_per_key=topk if kset else None)
    except Exception as e:
        return {"verdict": "not_in_lib", "confidence": 0.0, "evidence": [],
                "note": f"检索后端异常（{e.__class__.__name__}），本次未能核验。" + FIXED_NOTE}
    hits = [h for h in hits if not h.get("is_wiki")]   # wiki 综合页不许当核验证据（防自证循环）

    claim_grams = _bigrams(TL.norm_text(claim, fuzzy=True))
    import page_map as PM
    evidence, seen = [], set()
    for h in hits:
        text = h.get("context") or h.get("text") or ""
        quote, r = _best_sentence(claim_grams, text)
        if r < _OVERLAP_THR:
            continue                         # 空引文不是证据，不再返回给 agent 凑数
        pn = h.get("page")
        pr = ""
        if pn is not None and h.get("key"):
            try:
                pr = (PM.printed(h["key"], pn) or {}).get("display") or ""
            except Exception:
                pr = ""
        sig = (h.get("key", ""), pn, TL.norm_text(quote, fuzzy=True))
        if sig in seen:
            continue
        seen.add(sig)
        evidence.append({"key": h.get("key", ""), "title": h.get("title", ""),   # title 供人读（前端/agent 显示）
                         "pdf_page": pn, "printed_page": pr,
                         "quote": quote, "score": h.get("score", 0.0)})

    if not evidence:
        if hits:
            try:
                weak_conf = _norm_conf(hits[0].get("score", 0.0), api) * 0.5
            except Exception:
                weak_conf = 0.0
            return {"verdict": "not_in_lib", "confidence": round(weak_conf, 3), "evidence": [],
                    "note": "有主题相关命中，但未找到与论断高重叠的支撑句；空引文不作为证据返回。" + FIXED_NOTE}
        return {"verdict": "not_in_lib", "confidence": 0.0, "evidence": [],
                "note": "库内没有主题相关命中。" + FIXED_NOTE}

    # ── ③ top 命中的支撑句回原文精确定位页位（引注落到页，才对得起"核验"二字）──
    top = evidence[0]
    if top["quote"] and top["key"]:
        try:
            m = (TL.locate(top["quote"], key=top["key"], fuzzy=True, max_matches=1)
                 .get("matches") or [])
            if m:
                top["pdf_page"], top["printed_page"] = m[0]["pdf_page"], m[0]["printed_page"]
        except Exception:
            pass                            # 定位不到就保留 chunk 自带页位，不因锦上添花失败而炸

    # ── 三态判定（保守原则）──
    mode = R.STATE.get("mode")
    thr = _API_THR if api else _LOCAL_THR
    try:
        top_score = float(top["score"] or 0.0)
    except Exception:
        top_score = 0.0
    if mode == "full" and top["quote"] and top_score > thr:
        verdict = "supported"
        note = "检索命中显著且找到高重叠支撑句；请按 evidence 页位回原文复核表述与观点归属。"
        conf = _norm_conf(top_score, api)
    else:
        verdict = "not_in_lib"             # 软冲突/不显著/轻量模式：一律"未覆盖"，绝不判"为假"
        if mode != "full":
            note = "当前为轻量索引（仅题录，无正文），无法做支撑句核验，保守判为未覆盖。"
            conf = 0.0
        else:
            note = "找到候选支撑句但检索相关度未达显著阈值，保守判为未覆盖。"
            conf = _norm_conf(top_score, api)
    return {"verdict": verdict, "confidence": round(conf, 3), "evidence": evidence,
            "note": note + FIXED_NOTE}
