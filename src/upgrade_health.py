# -*- coding: utf-8 -*-
"""应用更新后仍留在用户目录中的内容：统一升级健康检查。

这里只做检测、提示和安全的模板分叉处理；不会自动重建索引、下载模型或覆盖用户文件。
"""
import ast, hashlib, json, os
from pathlib import Path

import config as C

APP = Path(__file__).resolve().parent

# 运行时判断只比较“索引产物契约”，不再比较实现文件。
# 实现文件指纹仅供发版前审计：代码变了但契约未声明时，check_guides 会阻止打包，
# 由发布者明确判断“契约不变”或提升契约版本，不把这个判断推给用户。
INDEX_CONTRACT_SCHEMA = 1
CURRENT_INDEX_CONTRACTS = {
    "light": "light-zotero-creators-v2",
    "deep": "deep-fulltext-chunks-v1",
    "semantic": "semantic-bge-m3-input-v1",
}
INDEX_CONTRACT_DETAILS = {
    "light": "题录字段、来源分类与轻量检索产物",
    "deep": "全文提取、页/章节定位与切块结构",
    "semantic": "题录及“检索摘要 + 正文块”的向量输入配方",
}

_IMPLEMENTATION_GROUPS = {
    "light": ("index_light.py", "zotero_source.py", "folder_source.py", "folder_meta.py",
              "source_rules.py", "journal_tiers.py", "journal_tiers.json", "legal_lexicon.py"),
    "deep": ("extract.py", "chunk.py", "page_map.py", "deep_extract_status.py"),
    "semantic": ("embed_index.py", "index_semantic.py", "embedder.py", "siliconflow_embedder.py"),
}

# 旧版清单只有“整份实现文件指纹”。这里一次性把已审计、产物语义兼容的历史指纹
# 翻译成稳定契约；迁移后运行时只看 index_contracts。
_LEGACY_FINGERPRINT_CONTRACTS = {
    "light": {
        "aa0f394ebb75dbd71f40480d1c02544c2f26a0f3155ad534763dc040f51537c6":
            CURRENT_INDEX_CONTRACTS["light"],
    },
    "deep": {
        # v1.0.17～v1.0.34 的 PDF 产物与五格式扩展后的既有 PDF 产物兼容。
        "961aa2cde7626605ccdd366aac8501469388d4c661252787661995153a41af30":
            CURRENT_INDEX_CONTRACTS["deep"],
        "acfdf51ca9e89f975d16e4d1d19babaadf21fe40b8d36df655fadec10a470252":
            CURRENT_INDEX_CONTRACTS["deep"],
    },
    "semantic": {
        # v1.0.19 起向量输入配方未变；其间差异均为进度、格式归一、身份记录、
        # 错误说明或未命中既有输入的长度保护。
        "0e99a082ea4af6d2edac105e076e60a510478673e84873d581346fb13849d9d3":
            CURRENT_INDEX_CONTRACTS["semantic"],
        "1a3e8c4ee3b4cc2c1a3f1ebb14162c4d8bfcabe715d36481d370d0b42bbd4bfe":
            CURRENT_INDEX_CONTRACTS["semantic"],
        "e8170f273feb5938dec66c2cf52bc0d699b69f7ec6e81b23e97f4b91e2749b15":
            CURRENT_INDEX_CONTRACTS["semantic"],
        "e601b71eb28c6030535d946fb354fb28cb8c3a61e06a6ae2005732a50f1dcc67":
            CURRENT_INDEX_CONTRACTS["semantic"],
        "93a677fb8f8ba9b8e7bae70e379b3a5316f6046c86e561e9633470ef64a4c09b":
            CURRENT_INDEX_CONTRACTS["semantic"],
        "c27d71643be6bdd04d15435be531f56051c85036a20728ef198ecedc8eb0d918":
            CURRENT_INDEX_CONTRACTS["semantic"],
    },
}

# 发布护栏的已审计实现快照。修改 _IMPLEMENTATION_GROUPS 内文件后，
# 若产物契约不变，把新实现指纹连同理由登记在当前契约下；若契约变了，先提升
# CURRENT_INDEX_CONTRACTS 对应 id。
_AUDITED_IMPLEMENTATIONS = {
    "light": {
        "light-catalog-v1": {
        "a840ea8220ae839a408353786ed9a412d69f6d8378236810f0c8d0c521c4b8dc":
            "改为稳定 light 契约登记，并防止旧应用覆盖更高版本契约；题录产物语义不变",
        },
        CURRENT_INDEX_CONTRACTS["light"]: {
        "b0bdb6feecdb83f50eb4cfc84e02bb390831870c45e82c2c1113a3cdfd386d11":
            "保留 Zotero creator 顺序、角色与机构身份，并按条目类型生成题录和引注；只需刷新轻量题录",
        },
    },
    "deep": {CURRENT_INDEX_CONTRACTS["deep"]: {
        "b4bb9b7cca3f8e396010c3ec067d3b15d9f612c100c61f40f58bd826d46c73d0":
            "仅增加切块契约防混用与原子落盘；提取、定位和切块算法不变",
    }},
    "semantic": {CURRENT_INDEX_CONTRACTS["semantic"]: {
        "689ad2d1e5277af32eeb24682427ed8103f60d3cc74e41b557b66ff7828278b7":
            "增加向量契约防混用与原子落盘，且不替切块阶段登记 deep 契约；向量输入规则不变",
    }},
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


def implementation_fingerprints():
    out = {}
    for group, names in _IMPLEMENTATION_GROUPS.items():
        h = hashlib.sha256()
        for name in names:
            h.update(name.encode("utf-8")); h.update(_sha_file(APP / name).encode("ascii"))
        out[group] = h.hexdigest()
    return out


def pipeline_fingerprints():
    """旧开发接口别名；仅供迁移测试/诊断，不再参与运行时索引健康判断。"""
    return implementation_fingerprints()


def unaudited_implementation_changes():
    """返回尚未经过契约审计的实现组；正式打包必须为空。"""
    current = implementation_fingerprints()
    out = {}
    for group, fingerprint in current.items():
        contract = CURRENT_INDEX_CONTRACTS[group]
        audited = _AUDITED_IMPLEMENTATIONS.get(group, {}).get(contract, {})
        if fingerprint not in audited:
            out[group] = {
                "contract": contract,
                "implementation_fingerprint": fingerprint,
            }
    return out


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_index_manifest(manifest):
    """索引阶段统一原子写清单，避免健康检查恰好读到半截 JSON。"""
    _atomic_json(C.INDEX_MANIFEST, manifest)


def manifest_contracts(manifest):
    """读取稳定契约；必要时从已审计的旧实现指纹翻译，绝不猜测未知指纹。"""
    raw = manifest.get("index_contracts")
    contracts = ({k: v for k, v in raw.items()
                  if k in CURRENT_INDEX_CONTRACTS and isinstance(v, str) and v}
                 if isinstance(raw, dict) else {})
    accepted = []
    unknown = []
    legacy = manifest.get("pipeline_fingerprints")
    if isinstance(legacy, dict):
        for group in CURRENT_INDEX_CONTRACTS:
            if group in contracts:
                continue
            old = legacy.get(group)
            contract = _LEGACY_FINGERPRINT_CONTRACTS.get(group, {}).get(old)
            if contract:
                contracts[group] = contract
                accepted.append(group)
            elif old:
                unknown.append(group)
    return contracts, accepted, unknown


def record_built_contract(manifest, group):
    """由真正生成该类产物的阶段调用；只更新这一组，不顺手洗白其它组。"""
    if group not in CURRENT_INDEX_CONTRACTS:
        raise KeyError(group)
    if manifest.get("index_contract_schema") not in (None, INDEX_CONTRACT_SCHEMA):
        raise ValueError("索引契约来自更新版本，当前应用不能覆盖")
    contracts, _accepted, _unknown = manifest_contracts(manifest)
    contracts[group] = CURRENT_INDEX_CONTRACTS[group]
    manifest["index_contract_schema"] = INDEX_CONTRACT_SCHEMA
    manifest["index_contracts"] = contracts
    return manifest


def group_contract_is_compatible(manifest, group, *, has_artifacts):
    """增量写入前的防混用闸门：无产物可直接建；有产物必须能证明契约兼容。"""
    if manifest.get("index_contract_schema") not in (None, INDEX_CONTRACT_SCHEMA):
        return False
    if not has_artifacts:
        return True
    contracts, _accepted, _unknown = manifest_contracts(manifest)
    return contracts.get(group) == CURRENT_INDEX_CONTRACTS[group]


def _group_has_artifacts(group):
    if group == "light":
        return C.PAPERS_JSONL.exists()
    if group == "deep":
        try:
            return next(C.CHUNKS.glob("*.json"), None) is not None
        except OSError:
            return False
    if group == "semantic":
        progress_files = (getattr(C, "META_EMBEDDED", None),
                          getattr(C, "EMBEDDED_KEYS", None),
                          getattr(C, "STATE", Path()) / "embedded_keys.txt")
        for path in progress_files:
            try:
                if path and Path(path).exists() and Path(path).stat().st_size > 0:
                    return True
            except OSError:
                pass
        try:
            return C.LANCEDB_DIR.exists() and any(C.LANCEDB_DIR.iterdir())
        except OSError:
            return False
    return False


def incompatible_built_groups(manifest, groups=("deep", "semantic")):
    """返回已有产物中与当前契约不兼容/无法核验的组，供检索和增量写入 fail closed。"""
    return [
        group for group in groups
        if _group_has_artifacts(group)
        and not group_contract_is_compatible(manifest, group, has_artifacts=True)
    ]


def index_health():
    if not C.INDEX_MANIFEST.exists():
        return {"state": "not_built", "label": "尚未建库", "action": "先完成建库"}
    try:
        manifest = json.loads(C.INDEX_MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:
        return {"state": "unknown", "label": "索引清单无法读取", "detail": str(e)}

    schema = manifest.get("index_contract_schema")
    if schema not in (None, INDEX_CONTRACT_SCHEMA):
        return {
            "state": "unknown",
            "label": "索引契约版本无法识别",
            "detail": "当前应用无法安全判断这份索引的生成规则；请先升级到最新版应用。",
            "action": "升级应用",
            "full_rebuild": False,
        }

    built, accepted, unknown = manifest_contracts(manifest)
    if accepted:
        manifest["index_contract_schema"] = INDEX_CONTRACT_SCHEMA
        manifest["index_contracts"] = built
        try:
            _atomic_json(C.INDEX_MANIFEST, manifest)
        except Exception as e:
            return {"state": "unknown", "label": "无法登记兼容的索引契约", "detail": str(e)}

    changed = []
    unverified = []
    for group, current_contract in CURRENT_INDEX_CONTRACTS.items():
        if built.get(group) == current_contract:
            continue
        if built.get(group) or group in unknown or _group_has_artifacts(group):
            changed.append(group)
            if not built.get(group):
                unverified.append(group)
    if not changed:
        result = {"state": "current", "label": "索引契约与当前版本一致"}
        if accepted:
            result["detail"] = "已自动登记兼容的历史索引契约，无需重新深索或重建向量。"
            result["accepted_migrations"] = [f"legacy-{x}" for x in accepted]
        return result
    if changed == ["light"]:
        return {"state": "stale", "label": "题录分类规则已更新",
                "changed": changed, "action": "手动更新知识库",
                "detail": "只需更新一次题录；无需清空索引、重新深索或重建语义索引。",
                "full_rebuild": False}
    need_full = any(k in changed for k in ("deep", "semantic"))
    if unverified:
        detail = ("现有索引缺少可核验的生成契约（"
                  + "、".join(INDEX_CONTRACT_DETAILS[x] for x in unverified)
                  + "），应用不会猜测或静默登记。")
        label = "索引生成契约无法确认"
    else:
        detail = ("以下索引产物契约与当前版本不同："
                  + "、".join(INDEX_CONTRACT_DETAILS[x] for x in changed) + "。")
        label = "索引产物契约已更新"
    return {"state": "stale", "label": label,
            "changed": changed, "action": "清空并从头重建索引" if need_full else "手动更新知识库",
            "detail": detail, "full_rebuild": need_full}


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
