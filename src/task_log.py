# -*- coding: utf-8 -*-
"""结构化运行日志 —— 任务级、可复核、可离线审计。

**为什么需要它**：应用原有的 `logs/` 是散装文本日志（谁打印谁负责措辞），
没有任务级结构，无法回答「这次研究任务一共调了哪些工具、各花多久、哪一步出错了、
模型被调用了多少次」。而审计与竞赛复核场景要的正是这个。

落点：`C.LOGS / "task_log.jsonl"`，一行一条 JSON，**只追加、不改写历史行**。
     （刻意复用既有的 C.LOGS，不新增 C.DATA 级路径常量 —— 那会触发 backup.py
      四清单与 check_guides ⑥ 的登记要求，见 CLAUDE.md §5。日志本就属可重建产物。）

**隐私纪律（重要）**：只记参数的**摘要**——键名、值长度、值的 sha1 前 8 位——
绝不记参数原文。检索词、论断原文、文件路径、密钥都可能出现在参数里，而日志是最容易
被整包外发的东西（竞赛要提交、报 bug 要贴）。需要复现某次调用时，用 sha1 前缀比对即可。

**失败静默**：所有写日志的动作都被 try/except 包住。**日志永远不能让主流程失败** ——
记不上就算了，比把一次真实的检索搞崩强。
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import config as C

_LOCK = threading.Lock()
_SEQ = 0
# 进程级运行号：同一次应用启动内的调用共享它，便于把一次会话的调用串起来看。
_RUN = time.strftime("%Y%m%d-%H%M%S")

# 模型调用计数器（嵌入 / 重排 / 生成）。由各引擎在自己的热路径上 +1，
# 工具调用结束时取差值，得到「这次调用期间模型被调了几次」。
_MODEL_CALLS = {"embed": 0, "rerank": 0, "llm": 0}


def log_file():
    return C.LOGS / "task_log.jsonl"


def bump(kind, n=1):
    """模型调用计数 +n。kind ∈ embed | rerank | llm。热路径调用，必须极轻且不抛。"""
    try:
        with _LOCK:
            if kind in _MODEL_CALLS:
                _MODEL_CALLS[kind] += n
    except Exception:
        pass


def _snapshot():
    try:
        with _LOCK:
            return dict(_MODEL_CALLS)
    except Exception:
        return {}


def _digest(args):
    """参数摘要：键名 + 类型 + 规模 + 内容哈希前缀。**不含原文。**"""
    out = {}
    try:
        for k, v in (args or {}).items():
            if isinstance(v, str):
                h = hashlib.sha1(v.encode("utf-8", "ignore")).hexdigest()[:8]
                out[k] = f"str[{len(v)}]#{h}"
            elif isinstance(v, (list, tuple)):
                out[k] = f"list[{len(v)}]"
            elif isinstance(v, dict):
                out[k] = f"dict[{len(v)}]"
            elif isinstance(v, bool) or v is None:
                out[k] = repr(v)          # 布尔与空值本身不敏感，原样记便于复核
            else:
                out[k] = f"{type(v).__name__}={v}"   # 数字类：topk / limit 之类，原样记有用
    except Exception:
        return {"_": "digest_failed"}
    return out


def next_task_id():
    global _SEQ
    try:
        with _LOCK:
            _SEQ += 1
            return f"T-{_RUN}-{_SEQ:04d}"
    except Exception:
        return f"T-{_RUN}-????"


def write(rec):
    """追加一条记录。失败静默。"""
    try:
        C.LOGS.mkdir(parents=True, exist_ok=True)
        with open(log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class step:
    """上下文管理器：包住一次工具调用，自动记录耗时、成败、模型调用数。

        with task_log.step("mcp", "search_localkb", args) as s:
            out = do_tool(name, args)
            s.note(hits=len(out))

    异常会被记录后**原样抛出** —— 这里只观测，不改变控制流。
    """

    def __init__(self, surface, tool, args=None):
        self.surface = surface
        self.tool = tool
        self.args = args
        self.task_id = next_task_id()
        self.extra = {}
        self._t0 = 0.0
        self._m0 = {}

    def note(self, **kw):
        try:
            self.extra.update(kw)
        except Exception:
            pass

    def __enter__(self):
        self._t0 = time.time()
        self._m0 = _snapshot()
        return self

    def __exit__(self, et, ev, tb):
        try:
            m1 = _snapshot()
            calls = {k: m1.get(k, 0) - self._m0.get(k, 0) for k in m1}
            rec = {
                "task_id": self.task_id,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "surface": self.surface,
                "tool": self.tool,
                "args": _digest(self.args),
                "ms": int((time.time() - self._t0) * 1000),
                "ok": et is None,
                "model_calls": {k: v for k, v in calls.items() if v},
            }
            if et is not None:
                rec["error"] = f"{et.__name__}: {str(ev)[:200]}"
            if self.extra:
                rec["result"] = self.extra
            write(rec)
        except Exception:
            pass
        return False        # 绝不吞异常


def tail(n=200):
    """读最近 n 条，供界面/复核使用。"""
    try:
        f = log_file()
        if not f.exists():
            return []
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
        out = []
        for l in lines:
            l = l.strip()
            if not l:
                continue
            try:
                out.append(json.loads(l))
            except Exception:
                pass
        return out
    except Exception:
        return []


def summary():
    """按工具聚合的统计，供复核时一眼看全貌。"""
    rows = tail(100000)
    agg = {}
    for r in rows:
        t = r.get("tool") or "?"
        a = agg.setdefault(t, {"n": 0, "fail": 0, "ms_total": 0, "embed": 0, "rerank": 0, "llm": 0})
        a["n"] += 1
        if not r.get("ok"):
            a["fail"] += 1
        a["ms_total"] += int(r.get("ms") or 0)
        for k in ("embed", "rerank", "llm"):
            a[k] += int((r.get("model_calls") or {}).get(k) or 0)
    for t, a in agg.items():
        a["ms_avg"] = int(a["ms_total"] / a["n"]) if a["n"] else 0
    return {"total": len(rows), "by_tool": agg}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    s = summary()
    print(f"运行日志：{log_file()}")
    print(f"共 {s['total']} 条")
    print(f"{'工具':28} {'次数':>5} {'失败':>4} {'均耗时ms':>9} {'嵌入':>5} {'重排':>5} {'生成':>5}")
    for t, a in sorted(s["by_tool"].items(), key=lambda kv: -kv[1]["n"]):
        print(f"{t:28} {a['n']:5} {a['fail']:4} {a['ms_avg']:9} {a['embed']:5} {a['rerank']:5} {a['llm']:5}")
