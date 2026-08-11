# -*- coding: utf-8 -*-
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class SourceGradingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_data = os.environ.get("LOCALKB_DATA")
        os.environ["LOCALKB_DATA"] = cls._tmp.name

        cls.SR = importlib.import_module("source_rules")
        cls.GS = importlib.import_module("grading_svc")
        cls.JG = importlib.import_module("journal_grading")
        cls.JG.reload()

        root = Path(cls._tmp.name)
        cls.SR.OVERRIDE_FILE = root / "tier_overrides.json"
        cls.GS.MEMO_FILE = root / "grading_memo.json"
        cls.GS.DIST_FILE = root / "grading_dist.json"
        cls.GS.MAPPING_FILE = root / "grading_mappings.json"

    @classmethod
    def tearDownClass(cls):
        if cls._old_data is None:
            os.environ.pop("LOCALKB_DATA", None)
        else:
            os.environ["LOCALKB_DATA"] = cls._old_data
        cls._tmp.cleanup()

    def setUp(self):
        try:
            self.SR.OVERRIDE_FILE.unlink()
        except FileNotFoundError:
            pass
        self.SR._OV = {"mtime": None, "data": {}}
        self.GS._MEMO = {}
        self.GS._MEMO_LOADED = True
        self.GS._MEMO_DIRTY = False
        self.GS._DIST = {}
        self.GS._DIST_LOADED = True
        try:
            self.GS.MAPPING_FILE.unlink()
        except FileNotFoundError:
            pass

    def ev(self, paper, discipline="law_personal"):
        return self.GS.evaluate_paper(paper, discipline=discipline)

    def test_non_journal_presets(self):
        cases = [
            ({"itemtype": "book", "title": "书"}, "书籍", "authority"),
            ({"itemtype": "bookSection", "title": "章"}, "书章", "authority"),
            ({"itemtype": "thesis", "title": "论文", "thesis_type": "博士"}, "博士论文", "top"),
            ({"itemtype": "thesis", "title": "论文", "thesis_type": "硕士"}, "硕士论文", "top"),
            ({"itemtype": "statute", "title": "某法"}, "法源", "top"),
            ({"itemtype": "case", "title": "某案"}, "案例", "top"),
            ({"itemtype": "standard", "title": "某标准"}, "标准", "top"),
            ({"itemtype": "report", "title": "研究报告"}, "研究报告", "top"),
            ({"itemtype": "preprint", "title": "预印本"}, "预印本", "normal"),
            ({"itemtype": "conferencePaper", "title": "会议论文"}, "会议论文", "normal"),
        ]
        for paper, label, band in cases:
            with self.subTest(paper=paper):
                got = self.ev(paper)
                self.assertEqual(label, got["objective_label"])
                self.assertEqual(band, got["band"])

    def test_dataset_and_container_content_override(self):
        official = self.ev({"itemtype": "dataset", "title": "人口数据", "institution": "国家统计局"})
        self.assertEqual(("权威数据", "core"), (official["objective_label"], official["band"]))
        ordinary = self.ev({"itemtype": "dataset", "title": "研究数据"})
        self.assertEqual(("数据集", "normal"), (ordinary["objective_label"], ordinary["band"]))

        report = self.ev({"itemtype": "webpage", "title": "未成年人司法保护白皮书",
                          "institution": "最高人民法院"})
        self.assertEqual(("report", "官方报告", "top"),
                         (report["source_type"], report["objective_label"], report["band"]))
        standard_report = self.ev({"itemtype": "report", "title": "研究报告"}, "law")
        self.assertEqual("core", standard_report["band"])
        law = self.ev({"itemtype": "newspaperArticle", "title": "最高人民法院关于审理某案的指导意见"})
        self.assertEqual(("legal_source", "法源", "top"),
                         (law["source_type"], law["objective_label"], law["band"]))

    def test_journal_unique_labels_and_weights(self):
        top3 = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中国法学"})
        self.assertEqual(("三大刊", "authority"), (top3["objective_label"], top3["band"]))

        clsci = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中外法学"})
        self.assertEqual(("CLSCI", "top"), (clsci["objective_label"], clsci["band"]))

        cssci_ext = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "上海金融"})
        pku = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "上海城市规划"})
        self.assertEqual(("CSSCI扩展", "core"), (cssci_ext["objective_label"], cssci_ext["band"]))
        self.assertEqual(("北大核心", "core"), (pku["objective_label"], pku["band"]))
        self.assertGreater(cssci_ext["weight"], pku["weight"])

    def test_tssci_and_taiwan_priority(self):
        # 2026-08-10：TSSCI 由 authority 改为 top，与引擎原型 tssci_law.收录 = T1b 对齐。
        ordinary = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "东吴法律学报"})
        self.assertEqual(("TSSCI", "top"), (ordinary["objective_label"], ordinary["band"]))

        highlighted = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "台湾大学法学论丛"})
        self.assertEqual("TSSCI", highlighted["objective_label"])
        self.assertEqual("top", highlighted["band"])

        personal_only = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "全国律师"})
        self.assertEqual(("台湾法学", "top"),
                         (personal_only["objective_label"], personal_only["band"]))

    def test_personal_factory_mapping_matches_user_preset(self):
        """出厂映射必须与引擎原型 A_法学个人 对得上（用户 2026-08-10 拍板改为跟随引擎）。

        旧预设（2026-06-28）与引擎逐条差 1—3 档，导致同一本刊"引擎说核心、界面显示权威"，
        且把 74.5% 的库挤进最高两档、四档加成只差 0.024，期刊权重事实上失效。
        改任何一条前先对照 grading_config.json 的 `A_法学个人`.map。
        """
        expected = {
            "label:SSCI Q1": "core", "label:SSCI Q2": "core",
            "label:SSCI Q3": "normal", "label:SSCI Q4": "normal",
            "label:CSSCI": "core", "nature:report": "top",
            "label:SJR Q1": "normal", "label:SJR Q2": "normal",
            "label:SJR Q3": "normal", "label:SJR Q4": "normal",
            "label:SSCI": "normal", "label:TSSCI": "top",
            "label:精选外文权威": "top", "label:台湾法学": "top",
        }
        original = self.GS._requested_disc
        try:
            self.GS._requested_disc = lambda: "law_personal"
            overview = self.GS.overview([])
            actual = {x["mapping_id"]: x["band"] for x in overview["mappings"]}
            self.assertEqual(expected, {k: actual[k] for k in expected})
            self.GS._requested_disc = lambda: "law_personal_fun"
            fun = self.GS.overview([])
            fun_actual = {x["mapping_id"]: x["band"] for x in fun["mappings"]}
            self.assertEqual(expected, {k: fun_actual[k] for k in expected})
        finally:
            self.GS._requested_disc = original

    def test_manual_override_changes_only_evaluation(self):
        paper = {"key": "p1", "itemtype": "journalArticle", "title": "文", "journal": "中国法学"}
        auto = self.ev(paper)
        self.assertEqual((auto["band"], auto["band_name"]),
                         (auto["auto_band"], auto["auto_band_name"]))
        self.SR.set_override("p1", "normal")
        manual = self.ev(paper)
        self.assertEqual(auto["objective_label"], manual["objective_label"])
        self.assertEqual("三大刊", manual["objective_label"])
        self.assertEqual("normal", manual["band"])
        self.assertEqual(("authority", "权威"),
                         (manual["auto_band"], manual["auto_band_name"]))
        self.assertTrue(manual["manual"])
        self.assertEqual("manual", manual["src"])
        self.SR.set_override("p1", None)
        restored = self.ev(paper)
        self.assertEqual(("authority", False), (restored["band"], restored["manual"]))

    def test_legacy_override_fold(self):
        expected = {"T1": "authority", "T1b": "top", "T2": "core", "T3": "core",
                    "T4": "normal", "T5": "normal"}
        for old, band in expected.items():
            key = "legacy-" + old
            self.SR.set_override(key, old)
            got = self.ev({"key": key, "itemtype": "book", "title": "书"})
            self.assertEqual(band, got["band"])
            self.assertEqual(old, got["internal_tier"])

    def test_fun_discipline_is_alias_with_display_only_difference(self):
        paper = {"itemtype": "journalArticle", "title": "文", "journal": "中国法学"}
        standard = self.ev(paper, "law_personal")
        fun = self.ev(paper, "law_personal_fun")
        for field in ("source_type", "objective_label", "band", "standard_band_name",
                      "internal_tier", "weight", "rank", "band_rank", "hit_catalogs"):
            self.assertEqual(standard[field], fun[field], field)
        self.assertEqual("权威", standard["band_name"])
        self.assertEqual("夯", fun["band_name"])
        self.assertEqual("law_personal", self.GS.canonical_discipline("law_personal_fun"))
        self.assertIn("law_personal", self.GS._MEMO)
        self.assertNotIn("law_personal_fun", self.GS._MEMO)

    def test_unknown_journal_is_normal_without_pending_label(self):
        got = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "完全不存在的某刊XYZ"})
        self.assertEqual("normal", got["band"])
        self.assertEqual("普通", got["standard_band_name"])
        self.assertNotEqual("待确认", got["band_name"])
        self.assertFalse(got["needs_review"])

    def test_mapping_override_is_per_discipline_and_keeps_objective_label(self):
        paper = {"itemtype": "book", "title": "书"}
        self.GS.set_mapping_override("nature:book", "normal", "law_personal")
        changed = self.ev(paper, "law_personal")
        untouched = self.ev(paper, "law")
        self.assertEqual(("书籍", "normal", "mapping"),
                         (changed["objective_label"], changed["band"], changed["src"]))
        self.assertEqual(("书籍", "authority"),
                         (untouched["objective_label"], untouched["band"]))
        self.GS.set_mapping_override("nature:book", None, "law_personal")
        self.assertEqual("authority", self.ev(paper, "law_personal")["band"])

    def test_default_value_is_not_stored_and_all_defaults_can_be_restored(self):
        # 与出厂值相同的选择不应制造“伪自定义”。娱乐显示名恢复的是共用的 law_personal 配置。
        self.GS.set_mapping_override("label:三大刊", "authority", "law_personal")
        self.assertNotIn("law_personal", self.GS._load_mapping_overrides())

        self.GS.set_mapping_override("label:SSCI Q4", "core", "law_personal")  # 出厂为 normal，故算自定义
        self.GS.set_mapping_override("nature:book", "normal", "law")
        result = self.GS.clear_mapping_overrides("law_personal_fun")
        saved = self.GS._load_mapping_overrides()
        self.assertEqual(("law_personal", 1),
                         (result["canonical_discipline"], result["removed"]))
        self.assertNotIn("law_personal", saved)
        self.assertEqual("normal", saved["law"]["nature:book"])
        restored = self.GS._apply_mapping_override({
            "objective_label": "SSCI Q4", "source_type": "journal_article",
            "band": "core", "band_name": "核心", "standard_band_name": "核心", "weight": 0.85,
        }, "law_personal")
        self.assertEqual("normal", restored["band"])   # 恢复出厂 = 跟随引擎的 T5

    # ── 2026-08-10 新增：identify 的 ISSN/刊名并集，与 field_focus 用户裁定目录 ──

    def test_issn_hit_still_picks_up_name_only_catalogs(self):
        """ISSN 命中后必须并入同刊的刊名桶信号（修复"同一本刊分裂成两档"）。

        历史 bug：clsci / law_review_top / ssci_law_authority / tw_law 四个私有目录的条目
        ISSN 字段是空的、只登记在 by_name 桶里，而 identify 原本 ISSN 一命中就 return，
        于是**题录里带 ISSN 的文献整条错过这些目录**。实测《中国社会科学》30 篇里
        28 篇（带 ISSN）被判顶级、2 篇（无 ISSN）判权威，同一本刊两个档。
        """
        ident = importlib.import_module("journal_grading.identify")
        data = self.JG.load_data()
        with_issn = ident.identify({"journal": "中国社会科学", "issn": "1002-4921"}, data)
        without = ident.identify({"journal": "中国社会科学"}, data)
        self.assertEqual("issn", with_issn.status)
        self.assertIn("clsci", with_issn.catalogs,
                      "带 ISSN 的题录必须仍能拿到只登记在刊名桶里的 clsci 信号")
        # 两条路径拿到的目录信号必须一致，否则同一本刊仍会按题录有无 ISSN 分裂
        self.assertEqual(without.catalogs.get("clsci"), with_issn.catalogs.get("clsci"))
        # 同一篇文献的最终档位也必须一致
        a = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中国社会科学",
                     "issn": "1002-4921"})
        b = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中国社会科学"})
        self.assertEqual(b["band"], a["band"])

    def test_issn_name_mismatch_does_not_merge_other_journals_signals(self):
        """题录的刊名与 ISSN 对不上时**不得**合并——否则把别的刊的目录信号安到本刊头上。"""
        ident = importlib.import_module("journal_grading.identify")
        data = self.JG.load_data()
        # 用《中国社会科学》的 ISSN 配一个完全不同的刊名（录错/子刊挂母刊 ISSN 的典型情形）
        mixed = ident.identify({"journal": "青少年犯罪问题", "issn": "1002-4921"}, data)
        self.assertEqual("issn", mixed.status)
        self.assertNotIn("field_focus", mixed.catalogs,
                         "刊名与 ISSN 不是同一本刊时，不得并入刊名侧的目录信号")

    def test_field_focus_is_user_verdict_and_outranks_official_catalogs(self):
        """field_focus 是用户对具体期刊的最终裁定：排 priorityOrder 第一位，能升也能降。"""
        data = self.JG.load_data()
        self.assertEqual("field_focus", data.resolution()["priorityOrder"][0])
        self.assertIn("field_focus", data.disciplines()["law_personal"]["catalogs"])
        # 出厂预置的两本本领域刊：此前无任何名录命中、兜底为"普通"
        top = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "青少年犯罪问题",
                       "issn": "1006-1509"})
        core = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "预防青少年犯罪研究",
                        "issn": "2095-3356"})
        self.assertEqual("top", top["band"])
        self.assertEqual("core", core["band"])
        # map 里每个 level 都要有对应 token，否则写了不生效且静默
        arch_map = data.archetype("A_法学个人")["map"]
        tokens = {t for toks in arch_map.values() for t in toks}
        for level in data.config["catalogs"]["field_focus"]["levels"]:
            self.assertIn(f"field_focus.{level}", tokens,
                          f"levels 里声明了「{level}」但 map 里没有对应 token，写了不会生效")

    def test_field_focus_really_outranks_cssci_and_clsci_both_directions(self):
        """真正的竞争用例：裁定必须压过官方名录，升降双向都生效。

        为什么必须这么测：出厂预置的两本刊在 cssci/clsci/pku/ssci/sjr 里一条都没有，
        objective_label 落「期刊论文」、天然绕开 `_apply_mapping_override`，所以只用它们
        测「outranks」是测不到东西的——本轮真有一个缺陷就是这样被放行的：
        `_objective_label` 原本不认识 field_focus，CSSCI 刊的标签仍是 "CSSCI"，
        于是 PERSONAL_MAPPING_DEFAULTS 无条件把 band 顶回 core，引擎判的 T1 被整个丢掉，
        用户的裁定在界面、排序、加成上一点变化都没有，且无任何提示。
        """
        import json as _json
        jdir = Path(os.environ["LOCALKB_DATA"]) / "journals"
        jdir.mkdir(parents=True, exist_ok=True)
        target = jdir / "field_focus.json"
        target.write_text(_json.dumps({
            "_meta": {"catalog": "field_focus"},
            "journals": [
                {"name": "法律适用", "issn": "", "level": "权威"},   # CSSCI 来源刊 → 抬到权威
                {"name": "中国法学", "issn": "", "level": "普通"},   # CLSCI 权威刊 → 压到普通
            ]}, ensure_ascii=False), encoding="utf-8")
        self.JG.reload()
        try:
            up = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "法律适用"})
            self.assertEqual("authority", up["band"], "裁定必须压过 CSSCI（升档）")
            down = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中国法学"})
            self.assertEqual("normal", down["band"], "裁定必须压过 CLSCI 权威（降档）")
            # 标签必须独立，否则又会落回 PERSONAL_MAPPING_DEFAULTS 被无条件覆盖
            for r in (up, down):
                self.assertTrue(str(r["objective_label"]).startswith("领域裁定"),
                                f"field_focus 命中时标签应独立，实际 {r['objective_label']!r}")
                self.assertNotIn(r["objective_label"], self.GS.PERSONAL_MAPPING_DEFAULTS)
        finally:
            target.unlink(missing_ok=True)
            self.JG.reload()

    def test_field_focus_accepts_both_formal_and_fun_band_names(self):
        """两套档名都认：正式(权威/顶级/核心/普通) 与娱乐(夯/顶级/人上人/NPC)。"""
        data = self.JG.load_data()
        arch_map = data.archetype("A_法学个人")["map"]
        pairs = [("权威", "夯", "T1"), ("核心", "人上人", "T2"), ("普通", "NPC", "T5")]
        for formal, fun, tier in pairs:
            self.assertIn(f"field_focus.{formal}", arch_map[tier])
            self.assertIn(f"field_focus.{fun}", arch_map[tier],
                          f"娱乐档名「{fun}」必须与正式名「{formal}」落到同一档")
        self.assertIn("field_focus.顶级", arch_map["T1b"])   # 两套同名
        # 娱乐学科只换显示名，档位与 law_personal 完全相同
        p = {"itemtype": "journalArticle", "title": "文", "journal": "青少年犯罪问题",
             "issn": "1006-1509"}
        self.assertEqual(self.ev(p)["band"], self.ev(p, discipline="law_personal_fun")["band"])

    def test_dist_cache_expires_when_grading_rules_change(self):
        """分布缓存必须跟着定档规则失效，否则库总览永远显示旧图。

        v1.1.0 实机暴露：逐刊 memo 有指纹会重算、文献卡档位是新的，而库总览的分布缓存
        只认 papers.jsonl 与改档文件的 mtime——题录没变就永远命中，怎么重启都不自愈。
        用户界面显示 482/540/191/217，同一份数据同一份代码现算是 548/526/161/195。
        """
        mt, ov = 123.0, 4.0
        fresh = {"v": self.GS.DIST_VER, "mtime": mt, "ov_mtime": ov,
                 "memo_ver": self.GS.MEMO_VER, "grading_sig": self.GS._grading_signature(),
                 "by_tier": [], "by_journal": []}
        self.assertTrue(self.GS._dist_fresh(fresh, mt, ov))
        # ① 老格式（升级前写的，没有这两个键）必须判为过期
        legacy = {k: v for k, v in fresh.items() if k not in ("memo_ver", "grading_sig")}
        self.assertFalse(self.GS._dist_fresh(legacy, mt, ov), "升级前的分布缓存必须失效")
        # ② 目录数据/配置变了（指纹变）必须判为过期
        self.assertFalse(self.GS._dist_fresh({**fresh, "grading_sig": "STALE"}, mt, ov))
        # ③ 代码侧口径 bump 必须判为过期
        self.assertFalse(self.GS._dist_fresh({**fresh, "memo_ver": self.GS.MEMO_VER - 1}, mt, ov))
        # ④ 原有的三把钥匙不能失灵
        self.assertFalse(self.GS._dist_fresh({**fresh, "v": self.GS.DIST_VER + 1}, mt, ov))
        self.assertFalse(self.GS._dist_fresh(fresh, mt + 1, ov))
        self.assertFalse(self.GS._dist_fresh(fresh, mt, ov + 1))

    def test_grading_signature_tracks_content_not_mtime(self):
        """指纹必须按目录**内容**算，不能按 mtime。

        2026-08-11 实机教训：初版用 (大小, mtime)，而安装器覆盖 app/ 时所有 catalogs
        文件都会重写、mtime 必变 —— 于是**每次升级都全库重算**，哪怕内容一字未改。
        用户从 1.1.0 升 1.1.1 时因此触发 42.8 秒重算，把 uvicorn 饿死、launcher 等不到
        /health，弹出「后台服务未能启动」。
        """
        import journal_grading.loader as L
        target = L.catalog_path("field_focus")
        self.assertTrue(target.exists(), "field_focus 目录应随包存在")

        self.GS._SIG_CACHE = None
        base = self.GS._grading_signature()
        self.assertTrue(base, "指纹不应为空")

        # 只动 mtime、不动内容 → 指纹必须不变（否则每次覆盖安装都会全库重算）
        st = target.stat()
        os.utime(target, (st.st_atime + 86400, st.st_mtime + 86400))
        try:
            self.GS._SIG_CACHE = None
            self.assertEqual(base, self.GS._grading_signature(),
                             "只改 mtime 不改内容时，指纹不得变化")
        finally:
            os.utime(target, (st.st_atime, st.st_mtime))
            self.GS._SIG_CACHE = None

        # 进程内要缓存：/stats 每次都会问它，不缓存就得反复读 ~2.7MB
        self.GS._SIG_CACHE = "SENTINEL"
        self.assertEqual("SENTINEL", self.GS._grading_signature())
        self.GS._SIG_CACHE = None

    def test_daemon_port_is_overridable_for_dev(self):
        """端口必须能用 LOCALKB_PORT 换掉，且非法值要安全回退。

        2026-08-11 实机教训：正式版常驻占着 8770，开发态再起一个实例就抢端口——
        launcher 绑不上、等不到 /health，弹「后台服务未能启动」，看起来像应用坏了。
        全项目只有 config 一处定义端口，换这一个环境变量就能让整条链路一起换。
        """
        import importlib
        C = importlib.import_module("config")
        # 直接测解析函数：**不要 reload config**——它会重算 DATA 等全局路径，
        # 把同批次里依赖这些路径的用例（test_upgrade_health）连带跑挂。
        old = os.environ.get("LOCALKB_PORT")
        try:
            for raw, expect in (("8771", 8771), ("", 8770), ("0", 8770),
                                ("99999", 8770), ("abc", 8770), ("  8899 ", 8899)):
                if raw:
                    os.environ["LOCALKB_PORT"] = raw
                else:
                    os.environ.pop("LOCALKB_PORT", None)
                self.assertEqual(expect, C._resolve_port(), f"LOCALKB_PORT={raw!r}")
        finally:
            if old is None:
                os.environ.pop("LOCALKB_PORT", None)
            else:
                os.environ["LOCALKB_PORT"] = old
        # URL 必须由 HOST/PORT 拼出，否则换端口时各处引用会分裂
        self.assertTrue(C.DAEMON_URL.endswith(f":{C.DAEMON_PORT}"))
        self.assertIn(C.DAEMON_HOST, C.DAEMON_URL)

    def test_authority_dataset_has_an_independent_mapping(self):
        authority = {"itemtype": "dataset", "title": "人口数据", "institution": "国家统计局"}
        ordinary = {"itemtype": "dataset", "title": "研究数据"}
        self.GS.set_mapping_override("nature:dataset_authority", "top", "law_personal")
        self.assertEqual("top", self.ev(authority)["band"])
        self.assertEqual("normal", self.ev(ordinary)["band"])


if __name__ == "__main__":
    unittest.main()
