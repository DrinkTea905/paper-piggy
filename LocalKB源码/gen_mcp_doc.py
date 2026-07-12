# -*- coding: utf-8 -*-
"""从 mcp_server.TOOLS 生成 MCP接入说明.md 里的工具表格 —— 工具清单的**单一事实源**。

背景：此前工具表是手写的，三处文档分别写 3 / 6 / 11 个工具，而代码里实际有 17 个，全对不上。
用法：改完 mcp_server.TOOLS 后跑一次
    python gen_mcp_doc.py            # 就地更新 MCP接入说明.md 的标记区块
    python gen_mcp_doc.py --check    # 只检查是否过期（CI/提交前用），过期则退出码 1
"""
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import mcp_server as M

DOC = Path(__file__).parent / "MCP接入说明.md"
BEGIN = "<!-- TOOLS:BEGIN 由 gen_mcp_doc.py 生成，勿手改 -->"
END = "<!-- TOOLS:END -->"

# 写工具（会改动 data/wiki 或触发建库）；其余为只读
WRITE_TOOLS = {"save_synthesis", "build_digest", "research_outline",
               "localkb_build", "deep_index", "mark_stale",
               "update_wiki_page", "set_wiki_links"}


def _sig(t):
    """从 inputSchema 拼出人类可读的签名：name(a, b=默认)"""
    props = (t.get("inputSchema") or {}).get("properties") or {}
    req = set((t.get("inputSchema") or {}).get("required") or [])
    parts = []
    for k, v in props.items():
        if k in req:
            parts.append(k)
        elif "default" in v:
            parts.append(f"{k}={v['default']}")
        else:
            parts.append(f"{k}?")
    return f"`{t['name']}({', '.join(parts)})`"


def _desc(t):
    """描述压成一行（表格单元格不能有换行/竖线）。"""
    d = " ".join((t.get("description") or "").split())
    return d.replace("|", "／")


def render():
    rows = ["| 工具 | 类型 | 作用 |", "|---|---|---|"]
    for t in M.TOOLS:
        kind = "写" if t["name"] in WRITE_TOOLS else "读"
        rows.append(f"| {_sig(t)} | {kind} | {_desc(t)} |")
    n_w = sum(1 for t in M.TOOLS if t["name"] in WRITE_TOOLS)
    head = f"共 **{len(M.TOOLS)} 个工具**（{len(M.TOOLS) - n_w} 读 / {n_w} 写）。本表由 `gen_mcp_doc.py` 从代码生成，不会与实现漂移。\n"
    return BEGIN + "\n" + head + "\n" + "\n".join(rows) + "\n" + END


def main():
    check = "--check" in sys.argv
    if not DOC.exists():
        print(f"找不到 {DOC}", file=sys.stderr)
        return 1
    txt = DOC.read_text(encoding="utf-8")
    block = render()
    if BEGIN in txt and END in txt:
        new = re.sub(re.escape(BEGIN) + r"[\s\S]*?" + re.escape(END), lambda _: block, txt, count=1)
    else:
        print(f"文档里没有 {BEGIN} … {END} 标记区块，请先手动加上。", file=sys.stderr)
        return 1
    if new == txt:
        print(f"OK：工具表已是最新（{len(M.TOOLS)} 个工具）。")
        return 0
    if check:
        print(f"过期：工具表与代码不一致（代码里有 {len(M.TOOLS)} 个工具）。跑 `python gen_mcp_doc.py` 更新。",
              file=sys.stderr)
        return 1
    DOC.write_text(new, encoding="utf-8")
    print(f"已更新 {DOC.name}（{len(M.TOOLS)} 个工具）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
