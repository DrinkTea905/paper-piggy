# -*- coding: utf-8 -*-
"""
文件夹模式：用 LLM 从 PDF 首 1-2 页文本抽题录（严格 JSON + 兜底）。
key 复用逻辑仿 sac._conf：folder_meta.key 空时自动复用 sac / api 的 key（一个 key 通吃）。
无法确定的字段留空；解析失败/超时 → 返回空 meta + needs_review（上层退文件名 title）。
"""
import sys, re, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import settings as S
import llm as L

SYS = ("你是文献题录抽取器。下面是一篇学术文献PDF的首1-2页文本。"
       "只输出一个JSON对象，字段：title(题名), author(作者，多个用'; '分隔), "
       "year(4位年份字符串), journal(期刊/出版物名), official_pages(正式页码如'1-20'，无则空), "
       "abstract(摘要，无则空), itemtype(journalArticle/book/thesis/report之一), "
       "langid(zh或en). 无法确定的字段留空字符串。不要输出JSON以外的任何内容。")


class NoKeyError(Exception):
    pass


def _conf():
    c = dict(S.folder_meta_conf())
    if not c.get("key"):
        for src in (S.sac_conf(), S.api_conf()):
            if src.get("key"):
                c["key"] = src["key"]
                c["base"] = c.get("base") or src.get("base")
                break
    return c


def available():
    c = _conf()
    return bool(c.get("enabled") and c.get("key"))


def _clean(v):
    return str(v).strip() if v is not None else ""


def _parse_json(raw):
    """剥离 ```json``` 围栏 + 提取第一个 {..} + json.loads。"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        s = m.group(0)
    return json.loads(s)


def extract_meta(head_text):
    """返回 (meta_dict, needs_review, err)。无 key 抛 NoKeyError；解析失败退空+needs_review。"""
    c = _conf()
    if not (c.get("enabled") and c.get("key")):
        raise NoKeyError("未配置 LLM，无法抽取题录")
    msgs = [{"role": "system", "content": SYS},
            {"role": "user", "content": (head_text or "")[:4000]}]
    try:
        raw = L.chat_once(msgs, c["base"], c["key"], c["model"], temperature=0.1, timeout=90)
        j = _parse_json(raw)
        meta = {k: _clean(j.get(k, "")) for k in
                ("title", "author", "year", "journal", "official_pages", "abstract", "itemtype", "langid")}
        ym = re.search(r"\d{4}", meta.get("year", "") or "")
        meta["year"] = ym.group(0) if ym else ""
        return meta, True, None
    except NoKeyError:
        raise
    except Exception as e:
        return {}, True, f"{type(e).__name__}: {e}"
