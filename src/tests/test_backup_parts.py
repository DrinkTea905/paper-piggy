"""Multi-ZIP recovery points remain complete and bounded, including file fragments."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_archive as A
import backup_parts as P
from backup_jobs import BackupError, BackupCancelled, defaults


class BackupPartsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.out = self.base / "out"
        self.job = defaults("zotero", "test-machine")
        self.job["destination"] = str(self.out)
        self.roots = [{"id": "zotero_data", "source": str(self.source)}]

    def create(self, limit=65536):
        return A.create(self.job, self.roots, max_part_size=limit)

    def index(self, result):
        return json.loads(Path(result["index_path"]).read_text(encoding="utf-8"))

    def save_index(self, result, value):
        Path(result["index_path"]).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def rewrite_part(self, result, number, change):
        index = self.index(result)
        path = Path(result["parts"][number - 1]["path"])
        with zipfile.ZipFile(path) as z:
            contents = {x.filename: z.read(x) for x in z.infolist()}
        change(contents)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, value in contents.items():
                z.writestr(name, value)
        index["parts"][number - 1]["size"] = path.stat().st_size
        index["parts"][number - 1]["sha256"] = A.file_sha256(path)
        self.save_index(result, index)

    def test_new_default_is_a_set_with_normal_zip_and_final_index(self):
        (self.source / "paper.pdf").write_bytes(b"ordinary paper")
        result = A.create(self.job, self.roots)
        self.assertEqual(3, result["archive_format"])
        self.assertEqual(2, result["manifest"]["format"])
        self.assertEqual(1, result["part_count"])
        self.assertEqual(P.MAX_PART_SIZE, self.index(result)["max_part_size"])
        self.assertEqual(result["size"], result["total_size"])
        self.assertEqual(result["size"], sum(Path(x["path"]).stat().st_size for x in result["parts"])
                         + Path(result["index_path"]).stat().st_size)
        with zipfile.ZipFile(result["path"]) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(b"ordinary paper", z.read("roots/zotero_data/paper.pdf"))
            self.assertEqual(3, json.loads(z.read("manifest.json"))["format"])
        self.assertFalse(list(self.out.glob("*.part")))

    def test_many_files_split_and_any_zip_finds_the_same_complete_set(self):
        data = {}
        for n in range(5):
            data[f"paper{n}.pdf"] = os.urandom(30000)
            (self.source / f"paper{n}.pdf").write_bytes(data[f"paper{n}.pdf"])
        result = self.create()
        self.assertGreater(result["part_count"], 1)
        for part in result["parts"]:
            self.assertLessEqual(Path(part["path"]).stat().st_size, 65536)
            self.assertIsNone(zipfile.ZipFile(part["path"]).testzip())
            info = A.inspect(part["path"])
            self.assertEqual(result["manifest"], info["manifest"])
            self.assertEqual(result["parts"], info["parts"])
            self.assertEqual(result["index_sha256"], info["index_sha256"])
        with A.open_archive(result["parts"][-1]["path"]) as reader:
            for name, value in data.items():
                self.assertEqual(value, reader.read("roots/zotero_data/" + name))
        self.assertEqual(5, A.inspect(result["index_path"])["n_entries"])

    def test_large_source_is_streamed_in_order_and_original_hash_is_checked(self):
        payload = os.urandom(190000)
        (self.source / "large.pdf").write_bytes(payload)
        result = self.create(32768)
        index = self.index(result)
        segments = index["file_segments"]["roots/zotero_data/large.pdf"]
        self.assertGreater(len(segments), 1)
        self.assertEqual(len(payload), sum(x["size"] for x in segments))
        self.assertEqual(hashlib.sha256(payload).hexdigest(), result["manifest"]["files"][0]["sha256"])
        with A.open_archive(result["parts"][-1]["path"]) as reader:
            with reader.open("roots/zotero_data/large.pdf") as stream:
                output = bytearray()
                while data := stream.read(137):
                    output.extend(data)
            self.assertEqual(payload, bytes(output))
        self.assertTrue(all(x["size"] <= 32768 for x in result["parts"]))
        self.assertEqual(payload, (self.source / "large.pdf").read_bytes())

    def test_zero_files_empty_directories_and_empty_source_are_valid(self):
        (self.source / "empty.pdf").touch()
        (self.source / "folder").mkdir()
        result = self.create(4096)
        info = A.inspect(result["path"])
        self.assertEqual(1, info["n_entries"])
        self.assertIn("roots/zotero_data/folder", info["manifest"]["directories"])
        with A.open_archive(result["path"]) as reader:
            self.assertEqual(b"", reader.read("roots/zotero_data/empty.pdf"))
        (self.source / "empty.pdf").unlink()
        result = self.create(4096)
        self.assertEqual(0, A.inspect(result["path"])["n_entries"])
        self.assertEqual(1, result["part_count"])

    def test_missing_index_and_missing_part_are_named_and_never_mutated(self):
        (self.source / "large.pdf").write_bytes(os.urandom(150000))
        result = self.create()
        part = Path(result["parts"][-1]["path"])
        kept = part.read_bytes()
        part.unlink()
        with self.assertRaises(BackupError) as error:
            A.inspect(result["path"])
        self.assertIn(part.name, str(error.exception))
        part.write_bytes(kept)
        index = Path(result["index_path"])
        index.unlink()
        with self.assertRaises(BackupError) as error:
            A.inspect(result["path"])
        self.assertIn(index.name, str(error.exception))
        self.assertTrue(part.is_file())

    def test_fragment_corruption_even_after_physical_rehash_is_rejected(self):
        (self.source / "large.pdf").write_bytes(os.urandom(100000))
        result = self.create()
        first = self.index(result)["file_segments"]["roots/zotero_data/large.pdf"][0]["member"]
        self.rewrite_part(result, 1, lambda values: values.update({first: b"x" * len(values[first])}))
        with self.assertRaisesRegex(BackupError, "片段校验"):
            A.inspect(result["path"])

    def test_part_from_another_set_cannot_be_substituted(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        first = self.create()
        second = self.create()
        destination = Path(first["path"])
        destination.write_bytes(Path(second["path"]).read_bytes())
        index = self.index(first)
        index["parts"][0].update(size=destination.stat().st_size, sha256=A.file_sha256(destination))
        self.save_index(first, index)
        with self.assertRaises(BackupError):
            A.inspect(first["path"])
        self.assertTrue(Path(second["index_path"]).is_file())

    def test_extra_part_outside_the_declared_set_is_ignored(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        first = self.create()
        second = self.create()
        self.assertTrue(A.inspect(first["path"])["ok"])
        self.assertTrue(A.inspect(second["path"])["ok"])

    def test_duplicate_part_and_escaping_index_path_are_rejected(self):
        (self.source / "large.pdf").write_bytes(os.urandom(100000))
        result = self.create()
        index = self.index(result)
        index["parts"].append(dict(index["parts"][0]))
        self.save_index(result, index)
        with self.assertRaises(BackupError):
            A.inspect(result["path"])
        result = self.create()
        def escape(values):
            marker = json.loads(values["manifest.json"])
            marker["index_name"] = "../outside.backup.json"
            values["manifest.json"] = json.dumps(marker).encode()
        self.rewrite_part(result, 1, escape)
        with self.assertRaises(BackupError):
            A.inspect(result["path"])

    def test_unlisted_path_and_special_member_rejected_even_with_physical_hash(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        for name in ("outside", "roots/zotero_data/../../escape"):
            result = self.create()
            self.rewrite_part(result, 1, lambda values: values.update({name: b"bad"}))
            with self.assertRaises(BackupError):
                A.inspect(result["path"])
        result = self.create()
        with zipfile.ZipFile(result["path"], "a") as z:
            entry = zipfile.ZipInfo("roots/zotero_data/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(entry, b"outside")
        index = self.index(result)
        p = Path(result["path"])
        index["parts"][0].update(size=p.stat().st_size, sha256=A.file_sha256(p))
        self.save_index(result, index)
        with self.assertRaises(BackupError):
            A.inspect(result["path"])

    def test_logical_kind_and_original_digest_are_validated(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        result = self.create()
        index = self.index(result)
        index["manifest"]["files"][0]["sha256"] = "f" * 64
        self.save_index(result, index)
        with self.assertRaisesRegex(BackupError, "哈希不匹配"):
            A.inspect(result["path"])
        result = self.create()
        index = self.index(result)
        index["manifest"]["kind"] = "paperpiggy"
        self.save_index(result, index)
        with self.assertRaises(BackupError):
            A.inspect(result["path"])

    def test_database_and_wal_can_cross_zip_files_and_are_checked_together(self):
        database = self.source / "zotero.sqlite"
        conn = sqlite3.connect(database)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE notes (body BLOB)")
        for _ in range(20):
            conn.execute("INSERT INTO notes VALUES (?)", (os.urandom(8192),))
        conn.commit()
        original = {p.name: p.read_bytes() for p in self.source.iterdir()}
        result = self.create(32768)
        self.assertGreater(result["part_count"], 1)
        checks = A.inspect(result["parts"][-1]["path"])["database_checks"]
        self.assertEqual("ok", checks[0]["integrity_check"])
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.source.iterdir()})

    def test_cancellation_and_write_failure_remove_only_this_uncommitted_set(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        good = self.create()
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        calls = []
        with self.assertRaises(BackupCancelled):
            A.create(self.job, self.roots, max_part_size=65536,
                     progress=lambda *_: calls.append(True), cancel=lambda: bool(calls))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})
        with mock.patch.object(P.os, "fsync", side_effect=OSError("test disk write failed")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})
        self.assertTrue(A.inspect(good["path"])["ok"])

    def test_partial_set_is_not_completed_and_size_limit_cannot_be_increased(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        for limit in (True, 0, 4095, P.MAX_PART_SIZE + 1):
            with self.assertRaises(BackupError):
                self.create(limit)
        with mock.patch.object(A, "_inspect_open", side_effect=BackupError("validation failed")):
            with self.assertRaisesRegex(BackupError, "validation failed"):
                self.create()
        self.assertFalse(list(self.out.iterdir()))

    def test_final_physical_limit_is_hard_checked_even_if_estimate_is_wrong(self):
        (self.source / "large.pdf").write_bytes(os.urandom(100000))
        with mock.patch.object(P, "_entry_bound", return_value=0):
            with self.assertRaisesRegex(BackupError, "最终大小超过"):
                self.create(65536)
        self.assertFalse(list(self.out.iterdir()))

    def test_limit_accounts_for_headers_not_only_original_file_sizes(self):
        (self.source / "large.pdf").write_bytes(os.urandom(65000))
        result = self.create(65536)
        self.assertGreater(result["part_count"], 1)
        self.assertTrue(all(Path(x["path"]).stat().st_size <= 65536 for x in result["parts"]))

    def test_exact_physical_limit_is_accepted_but_one_byte_less_is_rejected(self):
        (self.source / "paper.pdf").write_bytes(os.urandom(6000))
        result = self.create()
        index = self.index(result)
        index["max_part_size"] = Path(result["path"]).stat().st_size
        self.save_index(result, index)
        self.assertTrue(A.inspect(result["path"])["ok"])
        index["max_part_size"] -= 1
        self.save_index(result, index)
        with self.assertRaises(BackupError):
            A.inspect(result["path"])

    def test_duplicate_json_fields_are_not_silently_accepted(self):
        (self.source / "paper.pdf").write_bytes(b"data")
        result = self.create()
        path = Path(result["index_path"])
        original = path.read_bytes()
        path.write_bytes(b'{"format":3,' + original[1:])
        with self.assertRaisesRegex(BackupError, "重复字段"):
            A.inspect(result["path"])

    def test_format_two_archive_still_has_the_same_reader_interface(self):
        (self.source / "paper.pdf").write_bytes(b"old data")
        result = A._create_v2(self.job, self.roots)
        before = Path(result["path"]).read_bytes()
        info = A.inspect(result["path"])
        self.assertEqual(2, info["archive_format"])
        self.assertEqual(1, info["part_count"])
        self.assertEqual("", info["index_path"])
        self.assertEqual(before, Path(result["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
