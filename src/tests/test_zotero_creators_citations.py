# -*- coding: utf-8 -*-
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import cite_format as CF
import server
import zotero_source as ZS


class ZoteroCreatorFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.db = self.root / "zotero.sqlite"
        (self.root / "storage").mkdir()
        self.con = sqlite3.connect(self.db)
        self._schema()
        self._seed()
        self.con.commit()
        self.con.close()

    def _schema(self):
        self.con.executescript(
            """
            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER,
                dateAdded TEXT, libraryID INTEGER
            );
            CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
            CREATE TABLE deletedItems (itemID INTEGER);
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
            CREATE TABLE creators (
                creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT,
                fieldMode INTEGER
            );
            CREATE TABLE creatorTypes (
                creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT
            );
            CREATE TABLE itemCreators (
                itemID INTEGER, creatorID INTEGER, creatorTypeID INTEGER,
                orderIndex INTEGER
            );
            CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE itemTags (itemID INTEGER, tagID INTEGER);
            CREATE TABLE itemAttachments (
                parentItemID INTEGER, itemID INTEGER, path TEXT, contentType TEXT
            );
            CREATE TABLE collections (
                collectionID INTEGER PRIMARY KEY, collectionName TEXT
            );
            CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER);
            """
        )

    def _seed(self):
        self.con.executemany(
            "INSERT INTO itemTypes VALUES (?, ?)",
            [(1, "bookSection"), (2, "book"), (3, "report")],
        )
        self.con.executemany(
            "INSERT INTO creatorTypes VALUES (?, ?)",
            [(1, "author"), (2, "editor"), (3, "translator"),
             (4, "bookAuthor"), (5, "seriesEditor"), (6, "contributor")],
        )
        self.con.executemany(
            "INSERT INTO items VALUES (?, ?, ?, ?, 1)",
            [
                (1, "23A82AIY", 1, "2026-07-26 08:28:02"),
                (2, "TIXYV27U", 2, "2026-07-26 07:54:09"),
                (3, "6375T7EK", 3, "2026-07-26 08:12:45"),
            ],
        )
        self.con.executemany(
            "INSERT INTO creators VALUES (?, ?, ?, ?)",
            [
                (1, "Kathryn", "Hollingsworth", 0),
                (2, "James G.", "Dwyer", 0),
                (3, "Catriona", "Mackenzie", 0),
                (4, "Natalie", "Stoljar", 0),
                (5, "", "Committee on the Rights of the Child", 1),
                (6, "Book", "Author", 0),
                (7, "Series", "Editor", 0),
                (8, "Volume", "Contributor", 0),
            ],
        )
        self.con.executemany(
            "INSERT INTO itemCreators VALUES (?, ?, ?, ?)",
            [
                (1, 1, 1, 0),
                (1, 2, 2, 1),
                (1, 6, 4, 2),
                (1, 7, 5, 3),
                (1, 8, 6, 4),
                (2, 3, 2, 0),
                (2, 4, 2, 1),
                (3, 5, 1, 0),
            ],
        )
        values = {
            1: {
                "title": "Children and Juvenile Justice Law: The Possibilities of a Relational-Rights Approach",
                "date": "2020",
                "bookTitle": "The Oxford Handbook of Children and the Law",
                "pages": "775–802",
                "publisher": "Oxford University Press",
                "place": "New York",
                "abstractNote": "Existing abstract must be preserved.",
                "accessDate": "2026-07-26 08:28:02",
            },
            2: {
                "title": "Relational Autonomy: Feminist Perspectives on Autonomy, Agency, and the Social Self",
                "date": "2000",
                "publisher": "Oxford University Press",
                "place": "New York",
            },
            3: {
                "title": "General comment No. 12 (2009): The right of the child to be heard",
                "date": "2009",
                "institution": "United Nations",
                "reportNumber": "CRC/C/GC/12",
                "place": "Geneva",
                "numPages": "31",
            },
        }
        field_ids = {}
        value_id = 0
        for item_id, fields in values.items():
            for name, value in fields.items():
                if name not in field_ids:
                    field_ids[name] = len(field_ids) + 1
                    self.con.execute(
                        "INSERT INTO fields VALUES (?, ?)", (field_ids[name], name)
                    )
                value_id += 1
                self.con.execute(
                    "INSERT INTO itemDataValues VALUES (?, ?)", (value_id, value)
                )
                self.con.execute(
                    "INSERT INTO itemData VALUES (?, ?, ?)",
                    (item_id, field_ids[name], value_id),
                )


class ZoteroCreatorRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        ZoteroCreatorFixture(self.temp.name)
        self.papers = {
            p["key"]: p for p in ZS.load_papers(self.temp.name, library_id=1)
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_book_section_keeps_author_editor_order_and_existing_metadata(self):
        paper = self.papers["23A82AIY"]
        self.assertEqual("Kathryn Hollingsworth", paper["author"])
        self.assertEqual("Kathryn Hollingsworth", paper["authors"])
        self.assertEqual("James G. Dwyer", paper["editors"])
        self.assertEqual(
            ["author", "editor", "bookAuthor", "seriesEditor", "contributor"],
            [creator["role"] for creator in paper["creators"]]
        )
        self.assertEqual(
            ["Kathryn Hollingsworth", "James G. Dwyer", "Book Author",
             "Series Editor", "Volume Contributor"],
            [creator["name"] for creator in paper["creators"]],
        )
        self.assertEqual("775–802", paper["official_pages"])
        self.assertEqual("Existing abstract must be preserved.", paper["abstract"])
        self.assertEqual("2026-07-26 08:28:02", paper["access_date"])

    def test_edited_collection_has_editors_not_fake_authors(self):
        paper = self.papers["TIXYV27U"]
        self.assertEqual("", paper["author"])
        self.assertEqual("Catriona Mackenzie; Natalie Stoljar", paper["editors"])
        self.assertEqual(
            ["Catriona Mackenzie", "Natalie Stoljar"],
            [creator["name"] for creator in paper["creators"]],
        )

    def test_institutional_report_author_is_not_split_or_polluted(self):
        paper = self.papers["6375T7EK"]
        self.assertEqual("report", paper["itemtype"])
        self.assertEqual("Committee on the Rights of the Child", paper["author"])
        self.assertTrue(paper["creators"][0]["is_institution"])
        self.assertEqual("United Nations", paper["institution"])
        self.assertEqual("CRC/C/GC/12", paper["report_number"])
        self.assertEqual("", paper["publisher"])
        self.assertNotIn("numPages", paper)


class CitationTypeTests(unittest.TestCase):
    def setUp(self):
        self.book_section = {
            "key": "23A82AIY",
            "itemtype": "bookSection",
            "title": "Children and Juvenile Justice Law: The Possibilities of a Relational-Rights Approach",
            "author": "Kathryn Hollingsworth",
            "authors": "Kathryn Hollingsworth",
            "editors": "James G. Dwyer",
            "creators": [
                {"role": "author", "name": "Kathryn Hollingsworth"},
                {"role": "editor", "name": "James G. Dwyer"},
            ],
            "book_title": "The Oxford Handbook of Children and the Law",
            "journal": "The Oxford Handbook of Children and the Law",
            "place": "New York",
            "publisher": "Oxford University Press",
            "year": "2020",
            "official_pages": "775–802",
            "fulltext_format": "pdf",
        }
        self.edited_book = {
            "key": "TIXYV27U",
            "itemtype": "book",
            "title": "Relational Autonomy: Feminist Perspectives on Autonomy, Agency, and the Social Self",
            "author": "",
            "editors": "Catriona Mackenzie; Natalie Stoljar",
            "creators": [
                {"role": "editor", "name": "Catriona Mackenzie"},
                {"role": "editor", "name": "Natalie Stoljar"},
            ],
            "place": "New York",
            "publisher": "Oxford University Press",
            "year": "2000",
            "fulltext_format": "pdf",
        }
        self.report = {
            "key": "6375T7EK",
            "itemtype": "report",
            "title": "General comment No. 12 (2009): The right of the child to be heard",
            "author": "Committee on the Rights of the Child",
            "creators": [
                {
                    "role": "author",
                    "name": "Committee on the Rights of the Child",
                    "is_institution": True,
                }
            ],
            "institution": "United Nations",
            "report_number": "CRC/C/GC/12",
            "place": "Geneva",
            "publisher": "",
            "year": "2009",
            "fulltext_format": "pdf",
        }

    def test_book_section_has_human_roles_and_never_asks_for_issue(self):
        text = CF.footnote(self.book_section)
        for expected in (
            "Kathryn Hollingsworth",
            "James G. Dwyer主编",
            "Children and Juvenile Justice Law",
            "The Oxford Handbook of Children and the Law",
            "Oxford University Press2020年版",
            "第775–802页",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("期号", text)
        self.assertNotIn("issue", CF.missing_fields(self.book_section))
        with mock.patch.object(
            server, "_load_papers", return_value={"23A82AIY": self.book_section}
        ):
            result = server.cite_paper("23A82AIY")
        self.assertNotIn("issue", result["missing_fields"])

    def test_edited_collection_uses_editor_and_no_journal_requirements(self):
        text = CF.footnote(self.edited_book)
        self.assertIn("Catriona Mackenzie、Natalie Stoljar主编", text)
        self.assertIn("Oxford University Press2000年版", text)
        self.assertNotIn("载《", text)
        self.assertNotIn("待补期号", text)
        missing = CF.missing_fields(self.edited_book)
        self.assertNotIn("journal", missing)
        self.assertNotIn("issue", missing)
        self.assertNotIn("page", missing)

    def test_report_keeps_institution_and_report_number(self):
        text = CF.footnote(self.report)
        self.assertIn("Committee on the Rights of the Child", text)
        self.assertIn("CRC/C/GC/12", text)
        self.assertIn("Geneva：United Nations", text)
        self.assertNotIn("Oxford", text)
        missing = CF.missing_fields(self.report)
        self.assertNotIn("publisher", missing)
        self.assertNotIn("journal", missing)
        self.assertNotIn("page", missing)

    def test_thesis_has_its_own_branch(self):
        thesis = {
            "itemtype": "thesis",
            "title": "A Study",
            "author": "Alice Smith",
            "university": "Example University",
            "thesis_type": "博士",
            "year": "2023",
        }
        text = CF.footnote(thesis)
        self.assertIn("Example University博士学位论文", text)
        self.assertNotIn("待补期号", text)

    def test_journal_article_format_does_not_regress(self):
        article = {
            "itemtype": "journalArticle",
            "title": "普通期刊文章",
            "author": "张三; 李四",
            "journal": "法学研究",
            "year": "2024",
            "issue": "2",
            "official_pages": "10-20",
            "fulltext_format": "pdf",
        }
        self.assertEqual(
            "张三、李四：《普通期刊文章》，载《法学研究》2024年第2期，第10-20页。",
            CF.footnote(article),
        )
        self.assertEqual([], CF.missing_fields(article))

    def test_translated_book_and_three_person_roles(self):
        translated = {
            "itemtype": "book", "title": "少年司法制度",
            "creators": [
                {"role": "author", "name": "[美]巴里·C. 菲尔德"},
                {"role": "editor", "name": "甲"},
                {"role": "editor", "name": "乙"},
                {"role": "editor", "name": "丙"},
                {"role": "translator", "name": "高维俭"},
                {"role": "translator", "name": "蔡伟文"},
                {"role": "translator", "name": "任延峰"},
            ],
            "publisher": "中国人民公安大学出版社", "year": "2011", "edition": "2",
        }
        self.assertEqual(
            "〔美〕巴里·C. 菲尔德：《少年司法制度》，高维俭等译，"
            "中国人民公安大学出版社2011年第2版。",
            CF.footnote(translated),
        )

    def test_translated_journal_article_keeps_translators(self):
        article = {
            "itemtype": "journalArticle", "title": "译文", "author": "原作者",
            "translators": "译者甲; 译者乙", "journal": "法学译丛",
            "year": "2025", "issue": "3",
        }
        self.assertEqual(
            "原作者：《译文》，译者甲、译者乙译，载《法学译丛》2025年第3期。",
            CF.footnote(article),
        )

    def test_missing_issue_is_omitted_and_reported_without_placeholder(self):
        article = {"itemtype": "journalArticle", "title": "文章", "author": "张三",
                   "journal": "法学", "year": "2024"}
        text = CF.footnote(article)
        self.assertNotIn("__", text)
        self.assertNotIn("待补", text)
        self.assertIn("issue", CF.missing_fields(article))


if __name__ == "__main__":
    unittest.main()
