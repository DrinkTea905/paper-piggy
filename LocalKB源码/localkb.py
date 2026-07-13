# -*- coding: utf-8 -*-
"""
LocalKB 命令行（给 agent / 脚本用）——检索、建库、状态，一条命令搞定。
server 未起时自动后台拉起（首次加载模型约 30-60s，之后常驻秒回）。
任何 agent（Claude Code / Codex）用 Bash 调即可，无需记 HTTP。

⚠ 定位（EN-M6）：本 CLI 仅覆盖【检索 / 建库 / 状态】三件事。
完整能力（读原文、综合层 wiki 维护、引注排版与核验、找相似、收单篇等 28 个工具）
走 MCP：接入 mcp_server.py（见 MCP接入说明.md）。非 MCP 生态可直接调 HTTP，
OpenAPI 文档在 http://127.0.0.1:8770/docs。

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
        return requests.get(URL + "/health", timeout=3).json()
    except Exception:
        return None

def ensure_up(wait=120):
    h = health()
    if h and h.get("ready"):
        return True
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, str(C.APP / "server.py")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    print("[localkb] 启动检索服务（首次加载模型，请稍候）...", file=sys.stderr)
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(2)
        h = health()
        if h and h.get("ready"):
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
