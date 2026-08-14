# -*- coding: utf-8 -*-
"""★ 范例库真伪校验：逐段回原文核对，防止子代理编造「真刊原文」。

范例库是训练时的阅读对象。一旦混进编造的句子，整套 v5 就建在假地基上——
所以每一段摘录都必须能在 `D:\\PaperPiggy\\data\\extracted\\<key>.json` 的原始提取
正文里找到。

比对前双方都做同一套归一化（子代理誊录时清理了 PDF 的排版噪声，直接比对必然失败）：
  · 删除所有空白
  · 删除脚注上标序号（①-⑳、㉑-㊿、带圈数字、(1) 式角标）
  · 全角/半角标点统一，弯引号统一
  · 只保留汉字、拉丁字母、数字

判据：每段取**净化后的前 24 个汉字**在原文里查找；找不到则再退到前 16 字与
中段 16 字各试一次（应对跨页拼接与首句被截的情形），三次都找不到才判为失配。
"""
import json, io, sys, os, re, glob, unicodedata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "范例库")
EX = r"D:\PaperPiggy\data\extracted"

CIRCLED = "".join(chr(c) for c in list(range(0x2460, 0x2474)) + list(range(0x3251, 0x32C0))
                  + list(range(0x24EB, 0x24FF)) + list(range(0x2776, 0x2794)))
PUNCT = "，。、；：？！“”‘’（）《》〈〉【】—…·「」『』,.;:?!\"'()<>[]-~　 \t\r\n"


def norm(t):
    t = re.sub(r"\[\^?\d+\]|\(\d{1,3}\)|〔\d{1,3}〕", "", t)      # 角标
    t = "".join(ch for ch in t if ch not in CIRCLED)             # 圈号必须先删
    # ★ 中文期刊 PDF 常整篇使用全角数字与全角拉丁字母（叶小琴那篇全角数字 1157 处），
    #   而誊录时通常转成半角。不做 NFKC 会产生假阴性——2026-08-14 实测踩过一次。
    t = unicodedata.normalize("NFKC", t)
    t = "".join(ch for ch in t if ch not in PUNCT)
    return t


def load_src(key):
    p = os.path.join(EX, key + ".json")
    if not os.path.exists(p):
        return ""
    d = json.load(open(p, encoding="utf-8"))
    return norm("".join((pg.get("text") or "") for pg in d.get("pages") or []))


SEC = re.compile(r"^### 【([^】]+)】\s*(.*)$", re.M)

total = hit = 0
bad = []
print("%-12s %5s %5s %s" % ("篇目", "段数", "命中", "失配段（前 30 字）"))
for f in sorted(glob.glob(os.path.join(LIB, "*.md"))):
    key = os.path.splitext(os.path.basename(f))[0]
    src = load_src(key)
    if not src:
        print("%-12s  —— 原文加载失败" % key)
        continue
    txt = open(f, encoding="utf-8").read()
    parts = SEC.split(txt)
    # parts: [前言, tag1, rest1, body1, tag2, rest2, body2, ...]
    segs = []
    for i in range(1, len(parts) - 2, 3):
        tag, body = parts[i], parts[i + 2]
        body = re.split(r"^—\s*为什么值得当范例", body, maxsplit=1, flags=re.M)[0]
        segs.append((tag, body.strip()))
    n_ok, miss = 0, []
    for tag, body in segs:
        nb = norm(body)
        probes = [nb[:24], nb[:16], nb[len(nb) // 3:len(nb) // 3 + 16]]
        if any(p and p in src for p in probes):
            n_ok += 1
        else:
            miss.append((tag, body[:30].replace("\n", " ")))
    total += len(segs)
    hit += n_ok
    print("%-12s %5d %5d %s" % (key, len(segs), n_ok,
                                "" if not miss else miss[0][1] + (" …等%d段" % len(miss) if len(miss) > 1 else "")))
    bad += [(key, t, b) for t, b in miss]

print()
print("合计 %d 段，命中 %d 段，失配 %d 段（命中率 %.1f%%）"
      % (total, hit, total - hit, hit * 100.0 / max(total, 1)))
if bad:
    print("\n=== 失配明细（须逐条人工复核，不得默认是脚本问题）===")
    for k, t, b in bad:
        print("  [%s] 【%s】%s" % (k, t, b))
