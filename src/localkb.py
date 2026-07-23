# -*- coding: utf-8 -*-
"""
PaperPiggy 命令行（给 agent / 脚本用）——检索、建库、状态，一条命令搞定。
server 未起时自动后台拉起（首次加载模型约 30-60s，之后常驻秒回）。
任何 agent（Claude Code / Codex）用 Bash 调即可，无需记 HTTP。

⚠ 定位（EN-M6）：本 CLI 仅覆盖【检索 / 建库 / 状态】三件事。
完整能力（读原文、综合层 wiki 维护、引注排版与核验、找相似、收单篇……）走 MCP：
接入 mcp_server.py（工具清单见 MCP接入说明.md，由 gen_mcp_doc.py 从代码生成）。
非 MCP 生态可直接调 HTTP，OpenAPI 文档在 http://127.0.0.1:8770/docs。
（这里刻意不写工具个数——写死的数字必然漂移：它曾长期停在 28，而代码里已经是 32。）

用法:
  python localkb.py "认罪认罚从宽对司法信任"          # 检索，输出 JSON（results[]）
  python localkb.py "..." --topk 8 --sort tier        # 排序 relevance|tier|blend
  python localkb.py "..." --pretty                    # 人类可读
  python localkb.py --build light                     # 建库：light|semantic|deep
  python localkb.py --status                          # 服务/索引状态
"""
import sys, json, argparse, subprocess, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import requests

URL = C.DAEMON_URL

def health():
    try:
        data = requests.get(URL + "/health", timeout=3).json()
        return data if (isinstance(data, dict)
                        and data.get("app") == "paperpiggy"
                        and data.get("service") == "paperpiggy-local-api") else None
    except Exception:
        return None


def _pythonw_executable():
    current = Path(sys.executable)
    pyw = current if current.name.lower() == "pythonw.exe" else current.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else current)

def ensure_up(wait=120):
    # 判活按“服务是否应答”而非“索引 ready”：空库/重载时 ready 恒 False，旧逻辑会重复拉起进程并空等 120s。
    if health() is not None:
        return True
    subprocess.Popen([_pythonw_executable(), str(C.APP / "server.py")],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL, creationflags=C.SUBPROC_NO_WINDOW, close_fds=True)
    print("[localkb] 启动检索服务（首次加载模型，请稍候）...", file=sys.stderr)
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(2)
        h = health()
        if h is not None:   # 只等服务应答（空库 ready 恒 False；--build 正是要在空库上建，不能等 ready）
            print(f"[localkb] 就绪（{time.time()-t0:.0f}s, mode={h.get('mode')}, {h.get('n')} 条）", file=sys.stderr)
            return True
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--sort", default=None, choices=["relevance", "tier", "blend"])
    ap.add_argument("--build", choices=["light", "semantic", "deep"])
    ap.add_argument("--scope", default="all")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.status:
        print(json.dumps(health() or {"status": "down"}, ensure_ascii=False))
        return

    if args.build:
        if not ensure_up():
            print(json.dumps({"error": "server 启动失败"}, ensure_ascii=False)); sys.exit(2)
        ep = {"light": "/index/light", "semantic": "/index/semantic", "deep": "/index/deep"}[args.build]
        body = {"scope": args.scope} if args.build == "deep" else {}
        to = 900 if args.build == "light" else 10   # light 同步(可能几秒~几十秒)，其余后台起
        r = requests.post(URL + ep, json=body, timeout=to).json()
        print(json.dumps(r, ensure_ascii=False))
        return

    if not args.query:
        print("用法: python localkb.py \"查询词\"  |  --build light|semantic|deep  |  --status", file=sys.stderr)
        sys.exit(1)
    if not ensure_up():
        print(json.dumps({"error": "server 启动超时"}, ensure_ascii=False)); sys.exit(2)
    r = requests.post(URL + "/search", json={"query": args.query, "topk": args.topk, "sort": args.sort},
                      timeout=120).json()
    if args.pretty:
        print(f"查询「{args.query}」 mode={r.get('mode')} {r.get('took_ms')}ms")
        for i, x in enumerate(r.get("results", []), 1):
            print(f"\n[{i}] ({x.get('journal_tier')}) {x.get('citation')}")
            print(f"    {(x.get('text') or '')[:160].strip()}")
    else:
        print(json.dumps(r, ensure_ascii=False))

if __name__ == "__main__":
    main()
