# -*- coding: utf-8 -*-
"""少年司法论文初稿工作流 v2 全部 24 张 calibration-2 卡片检查。"""

import re
import unittest
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = REPO_ROOT / "docs" / "设计"
CARD_GLOB = "少年司法论文初稿工作流v2-写作工艺卡-[CD][0-9][0-9]-*.md"
EXPECTED = {
    "C01": "LIC6NDQD",
    "C02": "VHGF2QPQ",
    "C03": "TIZSWR6I",
    "C04": "F2EI6QQ8",
    "C05": "GXIRWF87",
    "C06": "I94VN74H",
    "C07": "PF6DLVQ3",
    "C08": "TMF76MJS",
    "D09": "52QG96AV",
    "D10": "WMMXTGHA",
    "D11": "9TA7JQQS",
    "D12": "6Z8PCVBF",
    "D13": "YBBABVE5",
    "D14": "UBBSPRAD",
    "D15": "V7ETFW8D",
    "D16": "5F7JYATI",
    "D17": "28ZHYN6P",
    "D18": "QSS2CY7H",
    "D19": "73SBPQTY",
    "D20": "G8ZGD3PT",
    "D21": "SAAI6XCH",
    "D22": "B9TRMQUA",
    "D23": "C4D3DFJT",
    "D24": "EWW6AFJP",
}
EXPECTED_RULINGS = {
    "C01": 9,
    "C02": 11,
    "C03": 8,
    "C04": 8,
    "C05": 11,
    "C06": 8,
    "C07": 9,
    "C08": 10,
    "D09": 8,
    "D10": 8,
    "D11": 8,
    "D12": 8,
    "D13": 8,
    "D14": 8,
    "D15": 8,
    "D16": 8,
    "D17": 6,
    "D18": 7,
    "D19": 7,
    "D20": 8,
    "D21": 8,
    "D22": 8,
    "D23": 8,
    "D24": 9,
}
ALLOWED_JUDGMENTS = ("作者明示", "结构推断", "训练综合", "not_present")
RULING_STATUSES = ("保留", "修正", "降级", "保留争议", "删除")
RULING_FIELDS = (
    "状态",
    "裁决分层",
    "争议",
    "支持理由",
    "反对理由",
    "重新打开原文页",
    "最终内容",
    "候选规则影响",
)
BANNED_PLACEHOLDER_VALUES = (
    "执行中",
    "待补",
    "同上",
    "待裁决",
    "待对抗",
    "TBD",
    "TODO",
)
METADATA_LABELS = (
    "样本编号",
    "PaperPiggy key",
    "作者",
    "题名",
    "年份",
    "期刊、卷期与印刷页范围",
    "当前期刊等级",
    "全文与深索状态",
    "主路线",
    "可叠加模块",
    "文章类型",
    "可能的结构原型",
    "页码口径",
    "篇幅换算口径",
    "原文提取代理",
    "结构对抗代理",
    "论证对抗代理",
    "语言与原创性代理",
    "是否独立重读",
    "最终裁决者",
)
ACTION_REQUIRED_LABELS = (
    "所处位置／主要功能",
    "主张／理由／材料",
    "从材料到结论的推理桥梁／反方如何被呈现、反方如何被限缩或回应",
    "段落内部动作顺序／怎样进入、怎样退出",
    "主张强度／限定语／转折方式／引注如何嵌入",
    "段落组粗略字数／粗略句数／引注位置",
    "印刷页码／最短必要原文定位／训练者重新表述后的通用动作",
    "判断性质",
    "不可迁移",
)
COVERAGE_ITEMS = (
    "引言的问题转折",
    "研究缺口",
    "中心命题或创新限定",
    "核心证明",
    "概念界定或类型划分",
    "最强反驳",
    "章节过渡",
    "结论回收",
)
FUNCTION_REQUIRED_LABEL_TOKENS = (
    "局部问题",
    "局部结论",
    "全文功能",
    "输入前提",
    "依据",
    "方法",
    "最强竞说",
    "输出到下一部分",
    "与前一部分",
    "与后一部分",
    "印刷结构",
    "真实推理结构",
    "章节关系类型",
    "删除本单元",
    "与相邻单元调换",
)
REVIEW_QUESTION_ALIASES = {
    "## 五、结构对抗": (
        ("现状—问题—完善",),
        ("推进中心命题",),
        ("印刷顺序",),
        ("作者个人习惯",),
        ("删除或调换",),
    ),
    "## 六、论证对抗": (
        ("换概念或标题", "创新兑现"),
        (
            "理论是否只是标签",
            "理论的操作化",
            "理论使用",
            "理论的必要性",
            "操作化",
            "必要性",
        ),
        (
            "证据与结论",
            "推理桥梁",
            "因果",
            "外推",
            "材料到制度动作",
        ),
        ("最强反方", "最强竞争理论"),
        (
            "规范解释与未来立法",
            "法层次",
            "现行法与改革方案",
            "现行体系解释与未来立法",
        ),
        (
            "描述、关联、因果与外推",
            "因果",
            "外推",
            "材料到制度动作",
            "向成人法迁移",
        ),
    ),
    "## 七、语言与原创性": (
        ("宏大空话",),
        ("引文串联",),
        ("主张强度",),
        ("标志性表达",),
        ("近似改写",),
        ("归属",),
    ),
}

INDEPENDENT_FIELD_ALIASES = (
    ("独立重读范围", "独立重读范围及代理"),
    ("独立形成的标题树与核心推理链", "独立标题树与核心推理链"),
    ("与主卡一致项",),
    ("分歧项", "与主卡分歧项"),
    ("指定回查页",),
    (
        "是否涉及字段缺失或页码问题",
        "字段缺失或页码问题",
        "字段或页码问题",
    ),
)


def _single_judgment_markers(text):
    return re.findall(r"〔([^；\n]+)；([^〕\n]+)〕", text)


def _ruling_blocks(text):
    matches = list(
        re.finditer(r"(?m)^### ([CD]\d{2}-R\d{2})\b.*$", text)
    )
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match.group(1), text[match.start():end]))
    return blocks


def _standalone_placeholders(text):
    """只拦截独立字段值，不误伤“待验证政策候选”等普通论证语义。"""
    pattern = re.compile(
        r"^`?(?:"
        + "|".join(re.escape(value) for value in BANNED_PLACEHOLDER_VALUES)
        + r")`?(?:[。.]|（[^）]*）|\([^)]*\))?$",
        re.IGNORECASE,
    )
    hits = []
    for line in text.splitlines():
        stripped = line.strip()
        values = []
        if stripped.startswith(("- ", "> ")) and "：" in stripped:
            values.append(stripped.split("：", 1)[1].strip())
        if stripped.startswith("|") and stripped.endswith("|"):
            values.extend(_table_cells(stripped))
        for value in values:
            if pattern.fullmatch(value):
                hits.append((line, value))
    return hits


def _is_independently_reread(text):
    match = re.search(
        r"(?m)^- 是否独立重读及(?:独立重读)?代理：(.+)$",
        text,
    )
    if not match:
        return False
    value = match.group(1).strip()
    return value.startswith("是") or value.startswith("已由独立重读代理")


def _locator_is_within_limit(locator):
    locator = locator.strip()
    if re.search(r"[\u3400-\u9fff]", locator):
        return len(re.sub(r"\s+", "", locator)) <= 25
    words = re.findall(r"[A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*", locator)
    return 0 < len(words) <= 25


def _cards():
    return sorted(DESIGN_DIR.glob(CARD_GLOB))


def _action_blocks(text):
    matches = list(re.finditer(r"(?m)^### (A\d{2})\｜[^\n]+$", text))
    blocks = []
    for index, match in enumerate(matches):
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            trailing_heading = re.search(
                r"(?m)^### 论证动作覆盖台账\s*$|^## 五、结构对抗\s*$",
                text[match.end():],
            )
            end = (
                match.end() + trailing_heading.start()
                if trailing_heading
                else len(text)
            )
        blocks.append((match.group(1), text[match.start():end]))
    return blocks


def _title_units(text):
    tree_start = text.index("### 1. 真实标题树")
    function_start = text.index("### 2. 逐单元功能记录")
    tree = text[tree_start:function_start]
    units = []
    for match in re.finditer(
        r"(?m)^\| (S[0-9]+) \| (一级|二级|无标题) \| ([^|]+) \|",
        tree,
    ):
        units.append(
            (match.group(1), match.group(2), match.group(3).strip())
        )
    return units


def _table_cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _function_records(text):
    """按 Sxx 收集逐单元小节或规范化联表记录。"""
    function_start = text.index("### 2. 逐单元功能记录")
    function_end = text.index("### 3. 全文推理链")
    function_text = text[function_start:function_end]
    records = defaultdict(list)

    section_matches = list(
        re.finditer(r"(?m)^#### (S[0-9]+)\｜[^\n]+$", function_text)
    )
    for index, match in enumerate(section_matches):
        end = (
            section_matches[index + 1].start()
            if index + 1 < len(section_matches)
            else len(function_text)
        )
        records[match.group(1)].append(
            ("section", function_text[match.start():end])
        )

    headers = None
    for line in function_text.splitlines():
        if line.startswith("| 单元 |"):
            headers = _table_cells(line)
            continue
        if headers and line.startswith("|---"):
            continue
        if headers and re.match(r"^\| S[0-9]+ \|", line):
            cells = _table_cells(line)
            records[cells[0]].append(("table", headers, cells, line))
            continue
        if headers and not line.startswith("|"):
            headers = None
    return records


class JuvenileJusticeWritingCraftRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = _cards()
        cls.texts = {
            path.name: path.read_text(encoding="utf-8")
            for path in cls.cards
        }

    def test_exact_24_card_training_set(self):
        expected_names = {
            f"少年司法论文初稿工作流v2-写作工艺卡-{sample}-{key}.md"
            for sample, key in EXPECTED.items()
        }
        self.assertEqual(len(self.cards), 24)
        self.assertEqual(set(self.texts), expected_names)
        for name, text in self.texts.items():
            sample_match = re.search(
                r"(?m)^- 样本编号：([CD]\d{2})$",
                text,
            )
            key_match = re.search(
                r"(?m)^- PaperPiggy key：`?([^`\n]+)`?$",
                text,
            )
            self.assertIsNotNone(sample_match, name)
            self.assertIsNotNone(key_match, name)
            sample = sample_match.group(1)
            key = key_match.group(1)
            self.assertEqual(EXPECTED.get(sample), key, name)
            self.assertTrue(name.endswith(f"-{sample}-{key}.md"), name)

    def test_metadata_and_final_state_are_complete(self):
        for name, text in self.texts.items():
            with self.subTest(card=name):
                self.assertRegex(text, r"(?m)^> 卡片状态：已裁决\s*$")
                self.assertIn(
                    "schema：`jj-writing-craft-card-v2-calibration-2`",
                    text,
                )
                for label in METADATA_LABELS:
                    self.assertRegex(
                        text,
                        rf"(?m)^- {re.escape(label)}[^\n：]*：\S",
                    )
                for progress in re.findall(
                    r"(?m)^> 裁决(?:状态|进度)：(.+)$",
                    text,
                ):
                    self.assertNotRegex(
                        progress,
                        r"执行中|对抗中|待裁决|尚未|未完成",
                        (name, progress),
                    )
                self.assertEqual(
                    _standalone_placeholders(text),
                    [],
                    name,
                )
                self.assertNotRegex(
                    text,
                    r"(?m)^- (?:结构对抗代理|论证对抗代理|"
                    r"语言与原创性代理|最终裁决者)："
                    r"(?:`?(?:待|not_present))",
                    name,
                )

    def test_all_judgment_markers_are_single_allowed_values(self):
        for name, text in self.texts.items():
            with self.subTest(card=name):
                markers = _single_judgment_markers(text)
                self.assertGreater(len(markers), 0)
                for judgment, page in markers:
                    self.assertIn(judgment, ALLOWED_JUDGMENTS)
                    self.assertNotIn("＋", judgment)
                    self.assertTrue(
                        bool(re.search(r"\d", page))
                        or judgment == "not_present"
                        or "检查范围" in page,
                        (name, judgment, page),
                    )
                all_brackets = re.findall(r"〔([^〕\n]+)〕", text)
                self.assertEqual(
                    len(all_brackets),
                    len(markers),
                    (name, "存在不含单一性质和页码的分析标签"),
                )

    def test_all_g_fields_exist_once_with_judgment_and_page(self):
        expected_fields = [f"G{i:02d}" for i in range(1, 36)]
        for name, text in self.texts.items():
            with self.subTest(card=name):
                rows = re.findall(r"(?m)^\| (G\d{2}) \| (.+) \|$", text)
                self.assertEqual([field for field, _ in rows], expected_fields)
                for field, content in rows:
                    self.assertGreaterEqual(
                        len(_single_judgment_markers(content)),
                        1,
                        (name, field, "缺单一判断性质或印刷页码"),
                    )
                    self.assertRegex(
                        content.rstrip(),
                        r"〔(?:作者明示|结构推断|训练综合|not_present)；[^〕]+〕[。.]?$",
                        (name, field, "行尾存在未被性质与页码覆盖的判断"),
                    )
                g35 = next(
                    content for field, content in rows if field == "G35"
                )
                self.assertRegex(g35, r"(?:2\s*万|20000)")
                self.assertRegex(g35, r"换算|粗算|粗略")

    def test_first_second_and_untitled_units_have_function_records(self):
        for name, text in self.texts.items():
            with self.subTest(card=name):
                self.assertNotRegex(text, r"(?m)^\| S\d+[-—]S\d+ \|")
                tree_start = text.index("### 1. 真实标题树")
                function_start = text.index("### 2. 逐单元功能记录")
                function_end = text.index("### 3. 全文推理链")
                tree_text = text[tree_start:function_start]
                function_text = text[function_start:function_end]
                self.assertIn(
                    "约合2万字稿字数",
                    tree_text.replace(" ", ""),
                )
                units = _title_units(text)
                self.assertGreater(len(units), 0)
                self.assertEqual(
                    len(units),
                    len({unit for unit, _, _ in units}),
                    (name, "标题树存在重复功能单元号"),
                )
                all_levels = re.findall(
                    r"(?m)^\| S[0-9]+[a-z]? \| ([^|]+) \|",
                    tree_text,
                )
                for level in all_levels:
                    self.assertIn(
                        level.strip(),
                        ("一级", "二级", "三级", "三级／四级", "无标题"),
                        (name, level),
                    )
                for line in tree_text.splitlines():
                    if re.match(r"^\| S[0-9]+[a-z]? \|", line):
                        cells = _table_cells(line)
                        self.assertGreaterEqual(len(cells), 7, (name, line))
                        budget = cells[5]
                        self.assertTrue(
                            bool(re.search(r"\d", budget))
                            or "not_applicable" in budget,
                            (name, cells[0], budget),
                        )
                records = _function_records(text)
                for unit, level, title in units:
                    if level == "无标题":
                        self.assertRegex(
                            title.strip("`"),
                            r"^not_present（[^）]*无标题[^）]*）$",
                            (name, unit, title),
                        )
                    unit_records = records.get(unit, [])
                    self.assertTrue(unit_records, (name, unit, level))
                    section_records = [
                        record for record in unit_records if record[0] == "section"
                    ]
                    table_records = [
                        record for record in unit_records if record[0] == "table"
                    ]
                    self.assertFalse(
                        section_records and table_records,
                        (name, unit, "同一单元混用小节和联表"),
                    )
                    if section_records:
                        self.assertEqual(len(section_records), 1, (name, unit))
                        block = section_records[0][1]
                        labels = [
                            line[2:].split("：", 1)[0]
                            for line in block.splitlines()
                            if line.startswith("- ") and "：" in line
                        ]
                        joined_labels = "／".join(labels)
                        for token in FUNCTION_REQUIRED_LABEL_TOKENS:
                            self.assertIn(token, joined_labels, (name, unit, token))
                        for line in block.splitlines():
                            if line.startswith("- "):
                                self.assertEqual(
                                    len(_single_judgment_markers(line)),
                                    1,
                                    (name, unit, line),
                                )
                    else:
                        self.assertGreaterEqual(len(table_records), 1)
                        joined_headers = "／".join(
                            header
                            for _, headers, _, _ in table_records
                            for header in headers[1:]
                        )
                        for token in FUNCTION_REQUIRED_LABEL_TOKENS:
                            self.assertIn(token, joined_headers, (name, unit, token))
                        for _, headers, cells, row in table_records:
                            self.assertEqual(
                                len(cells),
                                len(headers),
                                (name, unit, headers, cells),
                            )
                            self.assertGreaterEqual(
                                len(_single_judgment_markers(row)),
                                1,
                                (name, unit, row),
                            )

    def test_eight_action_cards_have_all_field_groups(self):
        for name, text in self.texts.items():
            with self.subTest(card=name):
                blocks = _action_blocks(text)
                self.assertEqual(
                    [identifier for identifier, _ in blocks],
                    [f"A{i:02d}" for i in range(1, 9)],
                )
                for identifier, block in blocks:
                    labels = [
                        line[2:].split("：", 1)[0]
                        for line in block.splitlines()
                        if line.startswith("- ") and "：" in line
                    ]
                    self.assertEqual(
                        len(labels),
                        len(ACTION_REQUIRED_LABELS),
                        (name, identifier, labels),
                    )
                    self.assertEqual(
                        set(labels),
                        set(ACTION_REQUIRED_LABELS),
                        (name, identifier, labels),
                    )
                    density_label = "段落组粗略字数／粗略句数／引注位置"
                    self.assertEqual(
                        [label for label in labels if label != density_label],
                        [
                            label
                            for label in ACTION_REQUIRED_LABELS
                            if label != density_label
                        ],
                        (name, identifier, "原有动作字段顺序发生变化"),
                    )
                    for line in block.splitlines():
                        if (
                            line.startswith("- ")
                            and not line.startswith("- 判断性质：")
                        ):
                            self.assertGreaterEqual(
                                len(_single_judgment_markers(line)),
                                1,
                                (name, identifier, "分析行缺性质或页码", line),
                            )
                            if "反方" in line and "not_present" in line:
                                self.assertTrue(
                                    any(
                                        token in line
                                        for token in ("原因", "未设置", "未提出", "检查")
                                    ),
                                    (name, identifier, "not_present反方缺原因"),
                                )
                    density_line = next(
                        (
                            line
                            for line in block.splitlines()
                            if line.startswith(
                                "- 段落组粗略字数／粗略句数／引注位置："
                            )
                        ),
                        "",
                    )
                    self.assertRegex(
                        density_line,
                        r"约[^／\n]*字／[^；。\n]*句[；。].+",
                        (name, identifier, density_line),
                    )
                    locator_line = next(
                        (
                            line
                            for line in block.splitlines()
                            if line.startswith(
                                "- 印刷页码／最短必要原文定位／"
                            )
                        ),
                        "",
                    )
                    quoted = re.findall(r"“([^”]+)”", locator_line)
                    self.assertTrue(quoted, (name, identifier, "缺最短定位"))
                    self.assertTrue(
                        _locator_is_within_limit(quoted[0]),
                        (name, identifier, quoted[0]),
                    )
                    page_part = locator_line.split("；", 1)[0]
                    self.assertRegex(
                        page_part,
                        r"\d.*页",
                        (name, identifier, page_part),
                    )

    def test_action_coverage_and_review_sections_exist(self):
        required_sections = (
            "## 五、结构对抗",
            "## 六、论证对抗",
            "## 七、语言与原创性",
            "## 八、独立重读",
            "## 九、原文裁决",
        )
        for name, text in self.texts.items():
            with self.subTest(card=name):
                for item in COVERAGE_ITEMS:
                    self.assertRegex(
                        text,
                        rf"(?m)^- {re.escape(item)}："
                        rf"(?:A\d{{2}}|`not_present(?:（[^`\n]+）)?`)",
                    )
                for section in required_sections:
                    self.assertIn(section, text)
                section_order = [
                    "## 五、结构对抗",
                    "## 六、论证对抗",
                    "## 七、语言与原创性",
                    "## 八、独立重读",
                    "## 九、原文裁决",
                ]
                for index, section in enumerate(section_order[:3]):
                    start = text.index(section)
                    end = text.index(section_order[index + 1])
                    review_text = text[start:end]
                    review_lines = [
                        line
                        for line in review_text.splitlines()
                        if re.match(r"^(?:- |\d+\.\s)", line)
                    ]
                    self.assertGreaterEqual(
                        len(review_lines),
                        len(REVIEW_QUESTION_ALIASES[section]),
                        (name, section, "对抗问题数量不足"),
                    )
                    for aliases in REVIEW_QUESTION_ALIASES[section]:
                        self.assertTrue(
                            any(alias in review_text for alias in aliases),
                            (name, section, aliases),
                        )
                    for line in review_lines:
                        self.assertGreaterEqual(
                            len(_single_judgment_markers(line)),
                            1,
                            (name, section, line),
                        )

                independent_start = text.index("## 八、独立重读")
                independent_end = text.index("## 九、原文裁决")
                independent_text = text[independent_start:independent_end]
                independent_labels = [
                    line[2:].split("：", 1)[0]
                    for line in independent_text.splitlines()
                    if line.startswith("- ") and "：" in line
                ]
                for aliases in INDEPENDENT_FIELD_ALIASES:
                    self.assertTrue(
                        any(
                            any(alias in label for alias in aliases)
                            for label in independent_labels
                        ),
                        (name, "独立重读字段缺失", aliases),
                    )
                for line in independent_text.splitlines():
                    if line.startswith("- ") and "：" in line:
                        self.assertRegex(line, r"：\S", (name, line))

                if not _is_independently_reread(text):
                    self.assertIn("not_present", independent_text, name)
                    self.assertTrue(
                        any(
                            token in independent_text
                            for token in (
                                "原因",
                                "冻结安排",
                                "未列入",
                                "未执行",
                                "尚未安排",
                                "不属于预定",
                            )
                        ),
                        (name, "未独立重读但缺原因"),
                    )

    def test_page_and_layout_anomaly_fields_are_explicit(self):
        for name, text in self.texts.items():
            with self.subTest(card=name):
                g32_match = re.search(
                    r"(?m)^\| G32 \| (.+) \|$",
                    text,
                )
                self.assertIsNotNone(g32_match, name)
                g32 = g32_match.group(1)
                self.assertTrue(
                    any(
                        token in g32
                        for token in (
                            "附件",
                            "版面",
                            "正文",
                            "摘要",
                            "参考文献",
                            "拼接",
                            "页",
                        )
                    ),
                    (name, g32),
                )
                self.assertGreaterEqual(
                    len(_single_judgment_markers(g32)),
                    1,
                    (name, "G32缺判断性质或核查页码"),
                )
                independent_start = text.index("## 八、独立重读")
                independent_end = text.index("## 九、原文裁决")
                independent_text = text[independent_start:independent_end]
                self.assertRegex(
                    independent_text,
                    r"(?m)^- (?:是否涉及)?字段(?:缺失)?或页码问题：\S",
                    name,
                )

    def test_rulings_have_single_status_and_eight_explicit_fields(self):
        for name, text in self.texts.items():
            sample_match = re.search(
                r"(?m)^- 样本编号：([CD]\d{2})$",
                text,
            )
            self.assertIsNotNone(sample_match, name)
            sample = sample_match.group(1)
            blocks = _ruling_blocks(text)
            self.assertEqual(
                len(blocks),
                EXPECTED_RULINGS[sample],
                (name, len(blocks)),
            )
            self.assertEqual(
                [identifier for identifier, _ in blocks],
                [
                    f"{sample}-R{index:02d}"
                    for index in range(1, EXPECTED_RULINGS[sample] + 1)
                ],
                name,
            )
            for identifier, block in blocks:
                fields = re.findall(
                    r"(?m)^- ("
                    + "|".join(re.escape(field) for field in RULING_FIELDS)
                    + r")：(\S.*)$",
                    block,
                )
                self.assertEqual(
                    len(fields),
                    len(RULING_FIELDS),
                    (name, identifier, fields),
                )
                self.assertEqual(
                    {field for field, _ in fields},
                    set(RULING_FIELDS),
                    (name, identifier, fields),
                )
                self.assertEqual(
                    _standalone_placeholders(block),
                    [],
                    (name, identifier),
                )
                status_match = re.search(
                    r"(?m)^- 状态：`?([^`\n]+)`?\s*$",
                    block,
                )
                self.assertIsNotNone(status_match, (name, identifier))
                self.assertIn(
                    status_match.group(1),
                    RULING_STATUSES,
                    (name, identifier, status_match.group(1)),
                )
                page_line = re.search(
                    r"(?m)^- 重新打开原文页：(.+)$",
                    block,
                ).group(1)
                self.assertTrue(
                    "页" in page_line and bool(re.search(r"\d", page_line)),
                    (name, identifier, page_line),
                )

    def test_24_card_training_batch_statistics(self):
        total_actions = 0
        total_g_fields = 0
        total_function_units = 0
        title_tree_levels = defaultdict(int)
        total_rulings = 0
        independent = 0
        for text in self.texts.values():
            total_actions += len(_action_blocks(text))
            total_g_fields += len(
                re.findall(r"(?m)^\| G\d{2} \|", text)
            )
            total_function_units += len(_title_units(text))
            tree_start = text.index("### 1. 真实标题树")
            function_start = text.index("### 2. 逐单元功能记录")
            tree_text = text[tree_start:function_start]
            for level in re.findall(
                r"(?m)^\| S[0-9]+[a-z]? \| ([^|]+) \|",
                tree_text,
            ):
                title_tree_levels[level.strip()] += 1
            total_rulings += len(_ruling_blocks(text))
            if _is_independently_reread(text):
                independent += 1
        self.assertEqual(total_actions, 192)
        self.assertEqual(total_g_fields, 840)
        self.assertEqual(total_function_units, 418)
        self.assertEqual(
            dict(title_tree_levels),
            {
                "一级": 117,
                "二级": 282,
                "三级": 79,
                "无标题": 19,
                "三级／四级": 1,
            },
        )
        self.assertEqual(sum(title_tree_levels.values()), 498)
        self.assertEqual(total_rulings, 199)
        self.assertGreaterEqual(independent, 8)
        self.assertGreaterEqual(independent / len(self.cards), 1 / 3)


if __name__ == "__main__":
    unittest.main()
