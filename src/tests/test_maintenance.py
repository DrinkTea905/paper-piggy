# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import maintenance as MT  # noqa: E402
import sac as SAC  # noqa: E402
import server  # noqa: E402
import settings as S  # noqa: E402
import upgrade_health as UH  # noqa: E402
import wiki_store as W  # noqa: E402


class MaintenanceAuditTests(unittest.TestCase):
    def test_compatible_deep_rule_change_is_accepted_without_rebuild(self):
        old_deep = "961aa2cde7626605ccdd366aac8501469388d4c661252787661995153a41af30"
        new_deep = "acfdf51ca9e89f975d16e4d1d19babaadf21fe40b8d36df655fadec10a470252"
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "index_manifest.json"
            manifest.write_text(json.dumps({"pipeline_fingerprints": {
                "light": "same-light", "deep": old_deep, "semantic": "same-semantic",
            }}), encoding="utf-8")
            with mock.patch.object(UH.C, "INDEX_MANIFEST", manifest), \
                    mock.patch.object(UH, "pipeline_fingerprints", return_value={
                        "light": "same-light", "deep": new_deep, "semantic": "same-semantic",
                    }):
                result = UH.index_health()
                saved = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual("current", result["state"])
        self.assertIn("无需重新深索", result["detail"])
        self.assertEqual(new_deep, saved["pipeline_fingerprints"]["deep"])

    def test_unknown_deep_rule_change_still_requires_full_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "index_manifest.json"
            manifest.write_text(json.dumps({"pipeline_fingerprints": {
                "light": "same-light", "deep": "unknown-old", "semantic": "same-semantic",
            }}), encoding="utf-8")
            with mock.patch.object(UH.C, "INDEX_MANIFEST", manifest), \
                    mock.patch.object(UH, "pipeline_fingerprints", return_value={
                        "light": "same-light", "deep": "unknown-new", "semantic": "same-semantic",
                    }):
                result = UH.index_health()
        self.assertEqual("stale", result["state"])
        self.assertTrue(result["full_rebuild"])
        self.assertEqual("清空并从头重建索引", result["action"])

    def test_light_rule_change_requires_only_metadata_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "index_manifest.json"
            manifest.write_text(json.dumps({"pipeline_fingerprints": {
                "light": "old", "deep": "same-deep", "semantic": "same-semantic",
            }}), encoding="utf-8")
            with mock.patch.object(UH.C, "INDEX_MANIFEST", manifest), \
                    mock.patch.object(UH, "pipeline_fingerprints", return_value={
                        "light": "new", "deep": "same-deep", "semantic": "same-semantic",
                    }):
                result = UH.index_health()
        self.assertEqual("题录分类规则已更新", result["label"])
        self.assertEqual("手动更新知识库", result["action"])
        self.assertFalse(result["full_rebuild"])
        self.assertIn("无需清空索引", result["detail"])

    def test_agent_mode_classifies_simple_work_as_auto_and_external_files_as_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); state = root / "state"; state.mkdir()
            papers = root / "papers.jsonl"
            papers.write_text(json.dumps({"key": "A", "title": "甲文"}, ensure_ascii=False) + "\n",
                              encoding="utf-8")
            (state / "wiki_suggestions.json").write_text(json.dumps({"items": [
                {"key": "A", "kind": "new_page", "status": "pending"},
                {"key": "OLD", "kind": "update", "status": "updated"},
            ]}), encoding="utf-8")
            with mock.patch.object(MT.C, "STATE", state), mock.patch.object(MT.C, "PAPERS_JSONL", papers), \
                    mock.patch.object(SAC, "summary_issues", return_value={"A": "摘要乱码"}), \
                    mock.patch.object(S, "sac_conf", return_value={"generator": "agent"}), \
                    mock.patch.object(UH, "health", return_value={"template_items": [
                        {"status": "pending", "key": "rely/参考格式/说明.md"}],
                        "index": {"state": "current"}}), \
                    mock.patch.object(W, "lint", return_value={"n_issues": 1, "issues": {}}):
                result = MT.audit_all({"sac_missing": 2, "invalid_pdf": 1})
            kinds = {x["kind"] for x in result["auto"]}
            self.assertEqual(kinds, {"template_merge", "agent_summaries", "wiki_suggestions", "wiki_lint"})
            self.assertEqual(result["wiki"]["pending"][0]["key"], "A")
            self.assertEqual(result["blocked"][0]["kind"], "invalid_pdf")
            self.assertFalse(result["complete"])

    def test_server_summary_mode_requires_decision_instead_of_auto_generation(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); papers = state / "papers.jsonl"; papers.write_text("", encoding="utf-8")
            with mock.patch.object(MT.C, "STATE", state), mock.patch.object(MT.C, "PAPERS_JSONL", papers), \
                    mock.patch.object(SAC, "summary_issues", return_value={}), \
                    mock.patch.object(S, "sac_conf", return_value={"generator": "server"}), \
                    mock.patch.object(UH, "health", return_value={"template_items": [], "index": {"state": "current"}}), \
                    mock.patch.object(W, "lint", return_value={"n_issues": 0, "issues": {}}):
                result = MT.audit_all({"sac_missing": 5})
            self.assertEqual(result["decision"][0]["kind"], "paid_summaries")
            self.assertFalse(any(x["kind"] == "agent_summaries" for x in result["auto"]))


class WikiSuggestionQueueTests(unittest.TestCase):
    def test_pending_items_are_not_truncated_and_resolution_is_auditable(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "wiki_suggestions.json"
            items = [{"key": f"K{i}", "status": "pending", "kind": "new_page"} for i in range(75)]
            with mock.patch.object(server, "_WIKI_SUGG_FILE", target):
                server._wiki_sugg_save(items)
                page = server.wiki_suggestions(status="pending", offset=50, limit=30)
                self.assertEqual(page["total"], 75)
                self.assertEqual(len(page["items"]), 25)
                result = server.wiki_suggestions_resolve(server.WikiSuggResolveQ(
                    key="K0", status="not_needed", reason="不形成可复用主题"))
                self.assertTrue(result["found"])
                self.assertEqual(server.wiki_suggestions(status="pending")["total"], 74)
                history = server.wiki_suggestions(status="all", limit=200)["items"]
                self.assertEqual(next(x for x in history if x["key"] == "K0")["status"], "not_needed")


class WikiHumanEditingTests(unittest.TestCase):
    def test_editable_body_hides_system_wrapper_and_keeps_user_markdown(self):
        meta = {"id": "p1", "kind": "digest", "title": "测试综述", "subject": "测试问题",
                "sources": [{"key": "K1", "citation": "来源一"}], "generated_at": "2026-07-22T00:00:00",
                "generated_by": "agent", "stale": False, "by_agent": True, "links": []}
        rendered = W._render_md(meta, "测试问题", "## 小节\n\n正文 [1]", meta["sources"])
        editable = W._editable_body(rendered, meta)
        self.assertEqual(editable, "## 小节\n\n正文 [1]")
        self.assertNotIn("参考来源", editable)
        self.assertNotIn("generated_at", editable)

    def test_human_edit_preserves_agent_origin_and_auto_verifies(self):
        existing = {"id": "p1", "kind": "digest", "title": "测试综述", "subject": "测试问题",
                    "sources": [{"key": "K1"}], "generated_by": "agent", "stale": True,
                    "by_agent": True, "links": ["p2"]}
        with mock.patch.object(W, "index_map", return_value={"p1": existing}), \
                mock.patch.object(W, "_persist_page", return_value={"id": "p1"}) as persist, \
                mock.patch.object(W, "set_verified", return_value={"verified_at": "2026-07-22T01:02:03"}) as verify:
            result = W.edit_page_by_human("p1", "修正后的正文")
        self.assertTrue(persist.call_args.kwargs["human_edit"])
        self.assertTrue(persist.call_args.kwargs["by_agent"])
        verify.assert_called_once_with("p1", True)
        self.assertEqual(result["verified_at"], "2026-07-22T01:02:03")

    def test_verified_state_can_be_cleared(self):
        meta = {"id": "p1", "kind": "digest", "title": "测试综述", "verified_at": "old"}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(W, "load_index", return_value={"pages": [meta]}), \
                mock.patch.object(W, "_save_index") as save_index, \
                mock.patch.object(W, "page_path", return_value=Path(td) / "missing.md"):
            result = W.set_verified("p1", False)
        self.assertFalse(result["verified"])
        self.assertEqual(result["verified_at"], "")
        self.assertNotIn("verified_at", meta)
        save_index.assert_called_once()


if __name__ == "__main__":
    unittest.main()
