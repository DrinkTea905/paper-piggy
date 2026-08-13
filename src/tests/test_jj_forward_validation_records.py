import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DOCS = ROOT / "docs" / "设计"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_ws as AW  # noqa: E402


PREFIX = "少年司法论文初稿工作流v2-正文级前向验证-"
BLIND_KEYS = (
    "SVNSFFZ3", "TXY75SMN", "XDVR96NW", "QIDFLN9E",
    "CMF3HLNP", "HIDEIB2X", "E9ZYIT5Z", "N3BY3BRD",
)


def doc(name):
    return DOCS / name


def read(name):
    return doc(name).read_text(encoding="utf-8")


class JuvenileForwardValidationRecordsTests(unittest.TestCase):
    def test_frozen_provenance_and_current_delivery_revision_are_explicit(self):
        frozen = read(f"{PREFIX}冻结清单.md")
        self.assertIn(
            "e0549417f34d64042525ccf581e669008f5b2041e4ad1553b0a16288d8c867ce",
            frozen,
        )
        self.assertIn(
            "64006a99fb61e8e068e3f1bb372307edee5cb1989c2c5b06f20e4b3215297328",
            frozen,
        )
        # v3（2026-08-03）整体替换 v2 的运行时文本；旧散列作为历史保留在冻结清单第一、四、五节。
        # 本断言跟随当前出厂常量，冻结清单**最后一节**必须同步记录同一组散列。
        # 当前值 = 2026-08-13 修订（第 1 步改为读记忆 core + 按主题取主题档案；推倒重来的题目不取旧档案），
        # 见冻结清单第七节。上一版（2026-08-10）散列
        # 7bf354c39b692f58d8c933f22a5e20e8c16abcf386febb17455703edbe04b199 转为历史，留在第六节。
        expected_current = {
            "workflow": "03cbe7a672d4dfa28a91a38bfd0ac2d07489bf2f9668728d5f8c134da90fb1f1",
            "handbook": "28f1bb30f95ac4af7d11bf0958ae9021de58e3e3782a64e3aa33be0e6814e903",
            "rules": "288f0760342cb00ef7450b8d95d8a1ab091f8f3e09891b3f587f112fa975e612",
            "tasks": "3d6f033ba98fd1d9375dceca800fe60349b100be2e7f697647fb6ce0255f8e58",
        }
        actual = {
            "workflow": hashlib.sha256(AW._WF_JJ_DRAFT.encode("utf-8")).hexdigest(),
            "handbook": hashlib.sha256(AW._JJ_DRAFT_CRAFT_HANDBOOK.encode("utf-8")).hexdigest(),
            "rules": hashlib.sha256(
                doc("少年司法论文初稿工作流v2-24篇规则冻结与运行时映射.md").read_bytes()
            ).hexdigest(),
            "tasks": hashlib.sha256(
                doc("少年司法论文初稿工作流v2-正文级前向验证任务集与判定表.md").read_bytes()
            ).hexdigest(),
        }
        self.assertEqual(expected_current, actual)
        # v3 措辞：卷首「最终成稿必须是可打开的 .docx」+「交付与沉淀」节的落点与检查要求
        self.assertIn("最终成稿必须是可打开的 `.docx`", AW._WF_JJ_DRAFT)
        self.assertIn("最终论文成稿固定写为 `0_Agent交付物/<主题>/<题目>.docx`", AW._WF_JJ_DRAFT)
        # v3 手册把 DOCX 检查并入 §7 成稿自检的可数判据表
        self.assertIn("DOCX 打开检查", AW._JJ_DRAFT_CRAFT_HANDBOOK)

    def test_six_raw_outputs_exist_and_do_not_contain_blind_keys(self):
        for run_id in ("D01", "D02", "T03", "T04", "M05", "S06"):
            path = doc(f"{PREFIX}V2-{run_id}-原始输出.md")
            self.assertTrue(path.is_file(), run_id)
            body = path.read_text(encoding="utf-8")
            self.assertGreater(len(body), 10_000, run_id)
            for key in BLIND_KEYS:
                self.assertNotIn(key, body, f"{run_id} accidentally used blind key {key}")

    def test_four_full_text_runs_have_required_craft_records(self):
        common = (
            "写作设计单", "声称创新", "结构原型",
            "摘要", "关键词", "引言", "结论",
            "来源—", "五轮",
        )
        for run_id in ("D01", "D02", "T03", "T04"):
            body = read(f"{PREFIX}V2-{run_id}-原始输出.md")
            for marker in common:
                self.assertIn(marker, body, f"{run_id} missing {marker}")
            self.assertTrue(
                "章功能表" in body or "章节功能表" in body,
                f"{run_id} missing chapter-function table",
            )
            self.assertTrue(
                "论证契约" in body or "论证合同" in body,
                f"{run_id} missing section argument contract",
            )
            self.assertTrue(
                "证据不足" in body or "不能证明" in body,
                f"{run_id} missing evidentiary limits",
            )

    def test_pre_route_runs_offer_two_real_cards_and_stop(self):
        for run_id, chosen in (("M05", "教义学"), ("S06", "理论—制度建构")):
            body = read(f"{PREFIX}V2-{run_id}-原始输出.md")
            pre, post = body.split("## 第二部分　选路后测试", 1)
            self.assertIn("路线方案卡 A：教义学", pre)
            self.assertIn("路线方案卡 B：理论—制度建构", pre)
            self.assertIn("推荐与停止点", pre)
            self.assertIn("用户尚未选择路线", pre)
            self.assertIn("不得生成摘要、引言或正文", pre)
            self.assertIn("独立的预设场景", body)
            self.assertIn(f"用户已经选择“{chosen}”路线", post)
            self.assertIn("写作设计单", post)
            self.assertIn("章节功能", post)
            self.assertIn("论证契约", post)
            for round_name in ("结构轮", "论证轮", "证据与引注轮", "语言轮", "原创性轮"):
                self.assertIn(round_name, post)

    def test_full_text_regressions_preserve_raw_and_add_budget_and_exact_rounds(self):
        raw_hashes = {
            "D01": "E96E9E316900582C1348E057FBBACE9C466641B010242DB59C1CA37EE897B5E1",
            "D02": "538E705CEF5C9AE6F5D0D326A2F307F6FA745E178211095FCA75AEB7DDDE608F",
            "T03": "C50F3C8CCD65185208D6E42AF7038D665A7155B9AEB0788D284C5A2FDA5473E3",
            "T04": "3FB7E13427868E1D4DBB1F91B2A10F5692B985C7C2FC23C93CF63E262A79D54D",
        }
        for run_id, expected_hash in raw_hashes.items():
            raw = doc(f"{PREFIX}V2-{run_id}-原始输出.md")
            self.assertEqual(expected_hash, hashlib.sha256(raw.read_bytes()).hexdigest().upper())
            regression = read(f"{PREFIX}V2-{run_id}-回归补充.md")
            self.assertIn(expected_hash, regression)
            self.assertIn("约2万字", regression)
            self.assertIn("原创性", regression)
            for round_name in ("结构", "论证", "证据与引注", "语言", "原创性"):
                self.assertIn(round_name, regression, f"{run_id} regression missing {round_name}")

    def test_pressure_run_does_not_overclaim_effectiveness(self):
        body = read(f"{PREFIX}V2-S06-原始输出.md")
        for marker in (
            "不能证明电子定位降低少年再犯",
            "技术性违约不是再犯的替代指标",
            "研究同意不能创造实体授权",
            "不冒充完整论文",
        ):
            self.assertIn(marker, body)

    def test_independent_scores_and_t03_regression_are_complete(self):
        research_scores = {
            "D01": 92, "D02": 90, "T03": 82,
            "T04": 90, "M05": 95, "S06": 94,
        }
        craft_scores = {
            "D01": (87,), "D02": (88,), "T03": (90,), "T04": (93,),
            "M05": (91, 93), "S06": (92,),
        }
        for run_id, score in research_scores.items():
            body = read(f"{PREFIX}V2-{run_id}-研究可靠性评分.md")
            self.assertIn(f"{score}/100", body, run_id)
            self.assertIn("硬失败", body, run_id)
            self.assertIn("未触发", body, run_id)
        for run_id, scores in craft_scores.items():
            body = read(f"{PREFIX}V2-{run_id}-写作工艺评分.md")
            for score in scores:
                self.assertIn(f"{score}/100", body, run_id)
            self.assertIn("硬失败", body, run_id)
            # 原为 assertIn("未", …)：单个"未"字在任何中文评分记录里都必然出现，是永真断言。
            # 真正要固定的是"硬失败一项都没触发"这个结论。
            self.assertIn("未触发", body, run_id)

        first = read(f"{PREFIX}V2-T03-研究可靠性评分.md")
        regression = read(f"{PREFIX}V2-T03-研究可靠性回归复评.md")
        self.assertIn("判定：未通过", first)
        self.assertIn("94/100", regression)
        self.assertIn("首次未通过；回归通过", regression)
        self.assertIn(
            "C50F3C8CCD65185208D6E42AF7038D665A7155B9AEB0788D284C5A2FDA5473E3",
            regression,
        )


if __name__ == "__main__":
    unittest.main()
