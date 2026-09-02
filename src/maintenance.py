# -*- coding: utf-8 -*-
"""知识库全量维护的只读盘点与完成判定。

这里不替 Agent 做内容判断，也不直接触发付费/破坏性操作；它只把散落在索引、
检索摘要、模板升级与 wiki 中的状态汇成一张可执行清单。真正的写操作仍走各自
已有的受保护接口。
"""
import json

import config as C


def _papers_by_stem():
    try:
        import textutil as T
        out = {}
        for line in C.PAPERS_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p = json.loads(line)
            key = str(p.get("key") or "")
            if key:
                out[T.safe_name(key)] = {"key": key, "title": p.get("title") or key}
        return out
    except Exception:
        return {}


def _wiki_suggestions():
    p = C.STATE / "wiki_suggestions.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("items", []) if isinstance(d, dict) else []
    except Exception:
        return []


MEMORY_DOMAIN_SOFT_MAX = 40000   # 单个工具经验子域的软上限：超了建议再分，不拒绝写入


def _memory_health():
    """记忆分区的健康度。

    记忆进维护清单的理由：它是 agent **每次开工都要整份读**的东西，长歪的代价比 wiki 长歪更大
    ——单向堆积的作业经验涨到十几万字时，「开工必读」实际上变成了「读第一段就开工」。
    这里顺带调 ensure_scaffold()，所以用户说一句「维护」，还没分区的工具经验就已经拆好了。
    """
    try:
        import agent_ws as AW
        AW.ensure_scaffold()
        core_chars, core_full, core_warn = AW.check_core_budget()
        doms = AW.list_memory_tooldoms()
        topics = AW.list_memory_topics()
        legacy = AW.read_memory_section("tools")[1]
        unfiled_path = AW.find_memory_tooldom(AW.MEMORY_TOOLDOM_FALLBACK)
        unfiled_n = (len(AW._split_tools_entries(unfiled_path.read_text(encoding="utf-8")))
                     if unfiled_path else 0)
        return {
            "core": {"chars": core_chars, "max": AW.MEMORY_CORE_MAX_CHARS,
                     "full": core_full, "warn": core_warn},
            "domains": doms,
            "unpartitioned": len(AW._split_tools_entries(legacy)),
            "unfiled": unfiled_n,
            "oversized": [d["name"] for d in doms if d["chars"] > MEMORY_DOMAIN_SOFT_MAX],
            "missing_when": [d["name"] for d in doms
                             if d["when"] == "未标注" or d["when"].startswith("（补一句")],
            "topics_unlabelled": [t["name"] for t in topics if t["status"] == "未标注"],
        }
    except Exception:
        return {}


def _memory_todo(mem):
    """记忆健康度 → 自动处理项。全是 agent 自己能做完的活，不涉及付费与删除。"""
    out = []
    if not mem:
        return out
    if mem.get("unpartitioned"):
        out.append({"kind": "memory_unpartitioned", "count": mem["unpartitioned"],
                    "action": "工具经验仍堆在单文件里（自动迁移没跑成）：按子域拆进 工具经验/，"
                              "每份写清「何时该读」，原文一字不改"})
    if mem.get("unfiled"):
        out.append({"kind": "memory_unfiled_tools", "count": mem["unfiled"],
                    "action": "逐条读「未归类」子域，改投到合适子域"
                              "（append_project_memory target=\"tools:<子域>\"），搬完把原条目删掉"})
    if mem.get("oversized"):
        out.append({"kind": "memory_domain_oversized", "count": len(mem["oversized"]),
                    "action": "子域过大（" + "、".join(mem["oversized"]) + "）：按更细的作业环节再分，"
                              "取一份正文的代价才降得下来"})
    if mem.get("missing_when"):
        out.append({"kind": "memory_domain_missing_when", "count": len(mem["missing_when"]),
                    "action": "补「何时该读」那一行（" + "、".join(mem["missing_when"]) + "）："
                              "core 索引全靠它决定要不要展开"})
    if mem.get("topics_unlabelled"):
        out.append({"kind": "memory_topic_unlabelled", "count": len(mem["topics_unlabelled"]),
                    "action": "主题档案缺状态行（" + "、".join(mem["topics_unlabelled"]) + "）："
                              "用 set_memory_topic_status 标 进行中/已完成/已作废"})
    core = mem.get("core") or {}
    if core.get("warn"):
        out.append({"kind": "memory_core_bloated", "count": core.get("chars", 0),
                    "action": f"项目记忆已 {core.get('chars')} 字（上限 {core.get('max')}）："
                              "把主题结论移进 主题档案/、把历史移进 变更日志.md，只留当前真相"})
    return out


def audit_all(index_status=None):
    """返回完整维护快照，并把问题分为自动处理/需决策/外部阻塞三类。"""
    import sac as SAC
    import settings as S
    import upgrade_health as UH
    import wiki_store as W

    idx = dict(index_status or {})
    upgrade = UH.health()
    lint = W.lint()
    suggestions = _wiki_suggestions()
    pending_suggestions = [x for x in suggestions if x.get("status", "pending") == "pending"]

    papers = _papers_by_stem()
    summary_issues = []
    for stem, reason in SAC.summary_issues().items():
        meta = papers.get(stem, {"key": stem, "title": stem})
        summary_issues.append({**meta, "stem": stem, "reason": reason})

    generator = S.sac_conf().get("generator", "off")
    auto, decision, blocked = [], [], []

    pending_templates = [x for x in upgrade.get("template_items", [])
                         if x.get("status") == "pending"]
    if pending_templates:
        auto.append({"kind": "template_merge", "count": len(pending_templates),
                     "action": "读取差异并合并；只有真实语义冲突才询问用户"})

    summary_gap = len(summary_issues) + int(idx.get("sac_missing") or 0)
    if summary_gap and generator == "agent":
        auto.append({"kind": "agent_summaries", "count": summary_gap,
                     "action": "Agent 读正文生成摘要，提交质量检查并重嵌入"})
    elif summary_gap and generator == "server":
        decision.append({"kind": "paid_summaries", "count": summary_gap,
                         "action": "估算服务端生成费用并征求确认"})

    if pending_suggestions:
        auto.append({"kind": "wiki_suggestions", "count": len(pending_suggestions),
                     "action": "逐条读来源并更新、建页或记录无需写入的理由"})
    if lint.get("n_issues"):
        auto.append({"kind": "wiki_lint", "count": lint.get("n_issues", 0),
                     "action": "修复可处理问题；无来源不等同于过时"})

    memory = _memory_health()
    auto.extend(_memory_todo(memory))

    index_health = upgrade.get("index") or {}
    if index_health.get("state") == "stale":
        decision.append({"kind": "rebuild_index", "count": 1,
                         "action": index_health.get("action") or "重建索引"})

    for key, label in (("missing_pdf", "PDF 附件缺失"), ("invalid_pdf", "PDF 损坏"),
                       ("missing_file", "全文附件缺失"), ("invalid_file", "全文附件无法读取"),
                       ("ocr_failed", "本地 OCR 失败")):
        n = int(idx.get(key) or 0)
        if n:
            blocked.append({"kind": key, "count": n, "label": label,
                            "action": "需要用户修复或替换源文件后重试"})

    return {
        "scope": "full",
        "rule": "用户只要提到维护，就全量审查；简单事项直接处理，需要用户决策的集中询问",
        "index": idx,
        "upgrade": upgrade,
        "summaries": {"generator": generator, "issues": summary_issues,
                      "missing": int(idx.get("sac_missing") or 0)},
        "wiki": {"pending": pending_suggestions, "all_items": suggestions, "lint": lint},
        "memory": memory,
        "auto": auto, "decision": decision, "blocked": blocked,
        "complete": not auto and not decision,
    }


def report_markdown(before, after, actions=None):
    """生成维护前后对照报告正文；调用方负责选择交付物路径。"""
    actions = actions or []
    lines = ["# 知识库全面维护报告", "", "## 执行原则", "",
             "用户提到维护后执行全量审查；简单事项直接处理，需决策事项单独确认。", "",
             "## 已执行", ""]
    lines.extend(f"- {x}" for x in actions)
    if not actions:
        lines.append("- 本轮仅完成审查，未发生写操作。")
    lines += ["", "## 前后对照", "",
              f"- 自动处理项：{len(before.get('auto', []))} → {len(after.get('auto', []))}",
              f"- 待用户决策：{len(before.get('decision', []))} → {len(after.get('decision', []))}",
              f"- 外部阻塞项：{len(after.get('blocked', []))}", "", "## 剩余事项", ""]
    for x in after.get("decision", []) + after.get("blocked", []):
        lines.append(f"- {x.get('kind')}：{x.get('count', 1)}；{x.get('action', '')}")
    if not after.get("decision") and not after.get("blocked"):
        lines.append("- 无。")
    return "\n".join(lines) + "\n"
