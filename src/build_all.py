# -*- coding: utf-8 -*-
"""
建库编排（按档触发）：
  --stage light      只即时词法（index_light，秒级）
  --stage semantic   只快速语义（index_semantic，1-2分钟）
  --stage deep       全文深索（extract→chunk→embed_index，数小时；可 --scope）
  --stage all        light→semantic（不自动跑 deep）
无参默认 all。各步断点续跑；反复运行 = 增量（Zotero 加文献→重跑→只补新增）。
用法: python build_all.py [--stage all] [--scope all] [--log x.log]
"""
import subprocess, sys, time, argparse, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

PY = sys.executable

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["light", "semantic", "deep", "all", "folder",
                             "deep_prepare", "deep_embed"])
    ap.add_argument("--scope", default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-stem", action="append", default=[])
    ap.add_argument("--log", default="")
    args = ap.parse_args()
    lim = ["--limit", str(args.limit)] if args.limit else []
    only = [arg for stem in args.only_stem for arg in ("--only-stem", stem)]

    LIGHT = ("即时词法", [PY, str(C.APP / "index_light.py")])
    SEM = ("快速语义", [PY, str(C.APP / "index_semantic.py")])
    CAT = ("收藏夹树", [PY, str(C.APP / "build_categories.py")])       # 只读 zotero.sqlite，无嵌入，秒级
    TOPICS = ("AI 主题", [PY, str(C.APP / "build_ai_topics.py")])      # 需要 meta 向量→放在 SEM 之后
    FOLDER_PREP = ("题录抽取", [PY, str(C.APP / "folder_ingest.py"), "--workers", str(args.workers)])  # folder 模式先补 meta_cache
    EXTRACT = ("提取全文附件", [PY, str(C.APP / "extract.py"), "--scope", args.scope, "--workers", str(args.workers)] + lim)
    CHUNK   = ("结构切块", [PY, str(C.APP / "chunk.py")] + lim)
    EMBED   = ("嵌入+索引", [PY, str(C.APP / "embed_index.py"), "--batch", "32"] + only + lim)
    PAGEMAP = ("印刷页码映射", [PY, str(C.APP / "page_map.py"), "--all"])   # 研究助手地基：PDF页→期刊印刷页
    DEEP = [EXTRACT, CHUNK, EMBED, PAGEMAP]
    # #7 Agent 驱动深索：拆两段，让「写摘要」插在 chunk 之后、embed 之前，一趟完成。
    #   deep_prepare = extract(--scope)+chunk（只切块不嵌入）→ 返回节选供 Agent 写摘要
    #   deep_embed   = embed_index+page_map（此时 summaries.json 已含 Agent 摘要→自动拼前缀）
    DEEP_PREPARE = [EXTRACT, CHUNK]
    DEEP_EMBED   = [EMBED, PAGEMAP]
    # CAT 跟 LIGHT（收藏夹一连库就该出现）；TOPICS 跟 SEM（要 meta 向量）。二者失败仅记日志、不阻断建库。
    # folder 阶段：先 FOLDER_PREP 补 meta_cache（含 N 次 LLM，分钟级），再 LIGHT（读 cache 建词法），再 SEM。
    # folder 不放 CAT（build_categories 只读 zotero.sqlite，folder 下会早退）。
    steps = {"light": [LIGHT, CAT], "semantic": [SEM, TOPICS], "deep": DEEP,
             "all": [LIGHT, CAT, SEM, TOPICS],
             "folder": [FOLDER_PREP, LIGHT, SEM, TOPICS],
             "deep_prepare": DEEP_PREPARE, "deep_embed": DEEP_EMBED}[args.stage]
    SOFT = {"收藏夹树", "AI 主题", "印刷页码映射"}   # 非致命：这些步骤失败只跳过、不 sys.exit

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logf = open(args.log, "a", encoding="utf-8") if args.log else None
    def emit(m):
        print(m, flush=True)
        if logf:
            logf.write(m + "\n"); logf.flush()

    # 任务五：强制子进程按 UTF-8 输出（PYTHONIOENCODING）并开启 UTF-8 模式，
    # 避免其 GBK 输出被上层按 UTF-8 解码成乱码（旧代码 env.pop("PYTHONUTF8") 反而触发乱码）。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    t0 = time.time()
    emit(f"\n===== 建库开始 stage={args.stage} {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    for name, cmd in steps:
        emit(f"[build] 开始：{name}")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             creationflags=C.SUBPROC_NO_WINDOW)   # ★ 不闪黑窗（extract/chunk/embed 子进程）
        for raw in p.stdout:
            emit(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        p.wait()
        if p.returncode != 0:
            if name in SOFT:
                emit(f"[build] 「{name}」跳过(code={p.returncode})，不影响建库。")
                continue
            emit(f"[build] 「{name}」失败(code={p.returncode})。修复后重跑即可续跑。")
            if logf: logf.close()
            sys.exit(p.returncode)
    emit(f"[build] BUILD_COMPLETE stage={args.stage}  用时 {(time.time()-t0)/60:.1f} 分钟")
    if logf: logf.close()

if __name__ == "__main__":
    main()
