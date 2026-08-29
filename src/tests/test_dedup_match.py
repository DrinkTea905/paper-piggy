"""dedup_match 的判定契约。

这些用例全部来自真实的差异形态：外部库（知网等）与 Zotero 在标点、全半角、
副标题、作者角色后缀上的系统性不一致。任何一条挂了，查重就会开始误判——
误判成 exists 会漏掉该补的文献，误判成 missing 会重复下载脏库。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dedup_match as D


LIB = [
    {"key": "a1", "title": "论未成年人社会调查报告的证据属性",
     "authors": "张三", "year": "2019", "journal": "中外法学"},
    {"key": "a2", "title": "附条件不起诉监督考察制度研究",
     "authors": "李四; 王五", "year": "2021", "journal": "法学研究"},
    {"key": "a3", "title": "Juvenile Justice and the Limits of Diversion",
     "authors": "Smith, John A.", "year": "2018", "journal": "Harvard Law Review"},
    {"key": "a4", "title": "少年司法的双重面孔", "authors": "赵六", "year": "2020",
     "journal": "政法论坛"},
]


class TestNormalize(unittest.TestCase):
    def test_punctuation_and_width_are_dropped(self):
        # 书名号、破折号、全角冒号、空格：外部库与 Zotero 的差异几乎都在这一层
        self.assertEqual(D.normalize_title("《少年司法》：新论"),
                         D.normalize_title("少年司法  新论"))
        self.assertEqual(D.normalize_title("ＡＩ与法"), D.normalize_title("AI与法"))

    def test_authors_chinese_whole_name_western_surname(self):
        self.assertEqual(D.normalize_authors("张三; 李四"), {"张三", "李四"})
        self.assertEqual(D.normalize_authors("张三 等著"), {"张三"})
        # 西文只留姓，因为 John / J. 的缩写方式各库不一
        self.assertIn("smith", D.normalize_authors("Smith, John A."))
        self.assertIn("smith", D.normalize_authors("John A. Smith"))


class TestVerdicts(unittest.TestCase):
    def setUp(self):
        self.idx = D.build_index(LIB)

    def test_exact_after_normalization_is_exists(self):
        r = D.match_one(self.idx, "论未成年人社会调查报告的证据属性", "张三", "2019")
        self.assertEqual(r["verdict"], "exists")
        self.assertEqual(r["matches"][0]["key"], "a1")

    def test_punctuation_only_difference_is_exists(self):
        r = D.match_one(self.idx, "论未成年人社会调查报告的证据属性 ", "张三", "")
        self.assertEqual(r["verdict"], "exists")

    def test_subtitle_appended_by_external_db_is_exists(self):
        # 知网常带副标题，Zotero 里往往只存正标题
        r = D.match_one(self.idx, "少年司法的双重面孔——以附条件不起诉为例", "赵六", "2020")
        self.assertEqual(r["verdict"], "exists")
        self.assertEqual(r["matches"][0]["key"], "a4")

    def test_western_title_matches_on_surname(self):
        r = D.match_one(self.idx, "Juvenile Justice and the Limits of Diversion",
                        "J. Smith", "2018")
        self.assertEqual(r["verdict"], "exists")

    def test_different_paper_same_topic_is_missing(self):
        # 同题不同篇：这正是语义检索会判错、而查重必须判对的情形
        r = D.match_one(self.idx, "论未成年人社会调查制度的实践困境", "钱七", "2023")
        self.assertEqual(r["verdict"], "missing")

    def test_similar_title_without_corroboration_is_probable(self):
        # 标题高度相似但作者、年份都对不上 —— 不敢下结论，交人眼
        r = D.match_one(self.idx, "附条件不起诉监督考察制度探究", "陈八", "2024")
        self.assertEqual(r["verdict"], "probable")
        self.assertTrue(r["matches"])

    def test_empty_title_is_missing_not_crash(self):
        self.assertEqual(D.match_one(self.idx, "")["verdict"], "missing")


class TestBatch(unittest.TestCase):
    def test_batch_preserves_order_and_counts(self):
        items = [
            {"title": "论未成年人社会调查报告的证据属性", "authors": "张三", "year": "2019"},
            {"title": "一篇库里绝对没有的文章标题abcdefg", "authors": "无名", "year": "2026"},
        ]
        out = D.check_batch(LIB, items)
        self.assertEqual([r["verdict"] for r in out["results"]], ["exists", "missing"])
        self.assertEqual(out["counts"]["exists"], 1)
        self.assertEqual(out["counts"]["missing"], 1)
        # 结果必须带回原标题，否则 Agent 无法把结果对回自己那批候选
        self.assertEqual(out["results"][0]["title"], items[0]["title"])

    def test_untitled_library_entries_do_not_break_index(self):
        out = D.check_batch(LIB + [{"key": "x", "title": ""}],
                            [{"title": "少年司法的双重面孔", "authors": "赵六"}])
        self.assertEqual(out["results"][0]["verdict"], "exists")


if __name__ == "__main__":
    unittest.main()
