# -*- coding: utf-8 -*-
"""应用更新后仍留在用户目录中的内容：统一升级健康检查。

这里只做检测、提示和安全的模板分叉处理；不会自动重建索引、下载模型或覆盖用户文件。
"""
import ast, hashlib, json, os
from pathlib import Path

import config as C

APP = Path(__file__).resolve().parent

_PIPELINE_GROUPS = {
    "light": ("index_light.py", "zotero_source.py", "folder_source.py", "folder_meta.py",
              "source_rules.py", "journal_tiers.py", "journal_tiers.json", "legal_lexicon.py"),
    "deep": ("extract.py", "chunk.py", "page_map.py", "deep_extract_status.py"),
    "semantic": ("embed_index.py", "index_semantic.py", "embedder.py", "siliconflow_embedder.py"),
}


def _sha_file(path):
    h = hashlib.sha256()
    try:
        if path.suffix == ".py":
            tree = ast.parse(path.read_text(encoding="utf-8"))

            class _NoDocstrings(ast.NodeTransformer):
                def _strip(self, node):
                    self.generic_visit(node)
                    if (getattr(node, "body", None) and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                            and isinstance(node.body[0].value.value, str)):
                        node.body = node.body[1:]
                    return node
                visit_Module = _strip
                visit_FunctionDef = _strip
                visit_AsyncFunctionDef = _strip
                visit_ClassDef = _strip

            h.update(ast.dump(_NoDocstrings().visit(tree), include_attributes=False).encode("utf-8"))
        elif path.suffix == ".json":
            h.update(json.dumps(json.loads(path.read_text(encoding="utf-8")),
                                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        else:
            with open(path, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
    except OSError:
        h.update(b"missing")
    return h.hexdigest()


def pipeline_fingerprints():
    out = {}
    for group, names in _PIPELINE_GROUPS.items():
        h = hashlib.sha256()
        for name in names:
            h.update(name.encode("utf-8")); h.update(_sha_file(APP / name).encode("ascii"))
        out[group] = h.hexdigest()
    return out


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def index_health():
    if not C.INDEX_MANIFEST.exists():
        return {"state": "not_built", "label": "尚未建库", "action": "先完成建库"}
    try:
        manifest = json.loads(C.INDEX_MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:
        return {"state": "unknown", "label": "索引清单无法读取", "detail": str(e)}
    current = pipeline_fingerprints()
    built = manifest.get("pipeline_fingerprints")
    if not isinstance(built, dict):
        # 首次引入指纹时，现有索引就是本版规则的基线；只补清单，不触发昂贵重建。
        manifest["pipeline_fingerprints"] = current
        try:
            _atomic_json(C.INDEX_MANIFEST, manifest)
        except Exception as e:
            return {"state": "unknown", "label": "无法记录索引规则版本", "detail": str(e)}
        return {"state": "current", "label": "索引规则已登记"}
    changed = [k for k, v in current.items() if built.get(k) != v]
    if not changed:
        return {"state": "current", "label": "索引与当前规则一致"}
    need_full = any(k in changed for k in ("deep", "semantic"))
    return {"state": "stale", "label": "索引由旧规则生成",
            "changed": changed, "action": "清空并从头重建索引" if need_full else "手动更新知识库",
            "full_rebuild": need_full}


def runtime_health():
    version_file = APP / "version.json"
    actual_file = APP.parent / "python" / ".paperpiggy-runtime.sha256"
    try:
        expected = json.loads(version_file.read_text(encoding="utf-8")).get("runtime_fingerprint", "")
    except Exception:
        expected = ""
    try:
        actual = actual_file.read_text(encoding="utf-8").strip()
    except Exception:
        actual = ""
    if not expected:
        return {"state": "untracked", "label": "当前安装方式不记录运行环境版本"}
    if expected == actual:
        return {"state": "current", "label": "运行环境与应用一致"}
    return {"state": "stale", "label": "运行环境需要随完整安装器更新",
            "action": "下载并覆盖安装最新版完整安装器"}


def _model_manifest_state():
    p = APP / "models_manifest.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {x.get("name"): x.get("sha256", "") for x in d.get("models", []) if x.get("name")}
    except Exception:
        return {}


def model_health():
    expected = _model_manifest_state()
    if not expected:
        return {"state": "unknown", "label": "模型清单不可用"}
    missing = [name for name in expected if not (C.MODELS / name / "model_quantized.onnx").exists()]
    if missing:
        return {"state": "missing", "label": f"缺少 {len(missing)} 个本地模型", "missing": missing,
                "action": "在设置向导中补下载模型"}
    state_file = C.MODELS / ".paperpiggy-models.json"
    try:
        installed = json.loads(state_file.read_text(encoding="utf-8")).get("models", {})
    except Exception:
        installed = None
    if not isinstance(installed, dict):
        try:
            _atomic_json(state_file, {"models": expected})
        except Exception as e:
            return {"state": "unknown", "label": "无法记录模型版本", "detail": str(e)}
        return {"state": "current", "label": "本地模型版本已登记"}
    outdated = [name for name, sha in expected.items() if installed.get(name) != sha]
    if outdated:
        return {"state": "stale", "label": f"有 {len(outdated)} 个模型可更新", "outdated": outdated,
                "action": "模型清单已变化；暂不自动覆盖，请按新版发布说明更新"}
    return {"state": "current", "label": "本地模型与当前清单一致"}


def health(include_ignored=False):
    import agent_ws as AW
    import wiki_store as W
    a = AW.upgrade_status(include_ignored)
    w = W.upgrade_status(include_ignored)
    items = a["items"] + w["items"]
    return {
        "pending_count": sum(x.get("status") == "pending" for x in items),
        "template_items": items,
        "index": index_health(), "runtime": runtime_health(), "models": model_health(),
    }


def diff(kind, key):
    if kind == "agent":
        import agent_ws as AW
        return AW.template_diff(key)
    if kind == "wiki" and key == "wiki/WIKI.md":
        import wiki_store as W
        return W.template_diff()
    raise KeyError("不支持的升级项")


def acknowledge(kind, key, current_hash):
    if kind == "agent":
        import agent_ws as AW
        return AW.acknowledge_update(key, current_hash)
    if kind == "wiki" and key == "wiki/WIKI.md":
        import wiki_store as W
        return W.acknowledge_update(current_hash)
    raise KeyError("不支持的升级项")


def replace(kind, key, current_hash):
    if kind == "agent":
        import agent_ws as AW
        return AW.replace_with_factory(key, current_hash)
    if kind == "wiki" and key == "wiki/WIKI.md":
        import wiki_store as W
        return W.replace_with_factory(current_hash)
    raise KeyError("不支持的升级项")


def merge(kind, key, current_hash, main_hash, merged_text):
    if kind == "agent":
        import agent_ws as AW
        return AW.merge_template(key, current_hash, main_hash, merged_text)
    raise KeyError("这一类规约不能由 Agent 自动合并")
