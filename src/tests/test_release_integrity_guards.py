# -*- coding: utf-8 -*-
"""更新、模型下载和安装包构建的完整性护栏回归测试。"""
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
ROOT = SRC.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "installer"))

import build_bundle as BB  # noqa: E402
import build_installer as BI  # noqa: E402
import models_bootstrap as MB  # noqa: E402
import updater  # noqa: E402


def digest(data):
    return hashlib.sha256(data).hexdigest()


class UpdateDownloadIntegrityTests(unittest.TestCase):
    def _info(self):
        base = "https://github.com/DrinkTea905/paper-piggy/releases/download/v9.9.9/"
        return {
            "has_update": True,
            "latest": "9.9.9",
            "url": base + "paper-piggy-app-9.9.9.zip",
            "sha256_url": base + "paper-piggy-app-9.9.9.zip.sha256",
        }

    def test_missing_official_sha_is_fail_closed(self):
        info = self._info()
        info["sha256_url"] = None
        with mock.patch.object(updater, "UPDATE_DIR", Path(tempfile.gettempdir()) / "unused-update"):
            result = updater.download(info, tries=1)
        self.assertFalse(result["ok"])
        self.assertIn("官方 sha256", result["error"])

    def test_mirror_sha_cannot_be_trust_root(self):
        info = self._info()
        info["sha256_url"] = "https://mirror.example/paper-piggy-app-9.9.9.zip.sha256"
        result = updater.download(info, tries=1)
        self.assertFalse(result["ok"])

    def test_hash_mismatch_never_returns_unverified_success(self):
        with tempfile.TemporaryDirectory() as td:
            info = self._info()
            wanted = digest(b"official")

            def fake_fetch(_url, dst, **_kwargs):
                Path(dst).write_bytes(b"tampered")

            with (mock.patch.object(updater, "UPDATE_DIR", Path(td)),
                  mock.patch.object(updater, "_fetch_text", return_value=wanted + "  asset.zip\n"),
                  mock.patch.object(updater, "_fetch_to", side_effect=fake_fetch),
                  mock.patch.object(updater, "_mirror_base", return_value="")):
                result = updater.download(info, tries=1)
            self.assertFalse(result["ok"])
            self.assertFalse(any(Path(td).glob("*.zip")))


class LocalModificationTests(unittest.TestCase):
    def test_app_scan_refuses_link_or_reparse_tree(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(updater, "_is_link_or_reparse", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "链接或重解析点"):
                updater._app_files(Path(td))

    def test_added_modified_deleted_are_all_reported(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td)
            (app / "same.py").write_bytes(b"same")
            (app / "changed.py").write_bytes(b"new")
            (app / "added.py").write_bytes(b"added")
            (app / "version.json").write_text(json.dumps({
                "version": "1",
                "files": {
                    "same.py": digest(b"same"),
                    "changed.py": digest(b"old"),
                    "deleted.py": digest(b"deleted"),
                },
            }), encoding="utf-8")
            report = updater.local_modification_report(app)
        self.assertTrue(report["manifest_ok"])
        self.assertEqual(report["added"], ["added.py"])
        self.assertEqual(report["modified"], ["changed.py"])
        self.assertEqual(report["deleted"], ["deleted.py"])

    def test_broken_manifest_preserves_every_real_file(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td)
            (app / "one.py").write_text("x=1", encoding="utf-8")
            (app / "asset.txt").write_text("x", encoding="utf-8")
            (app / "version.json").write_text("not-json", encoding="utf-8")
            report = updater.local_modification_report(app)
        self.assertFalse(report["manifest_ok"])
        self.assertEqual(report["added"], ["asset.txt", "one.py"])

    def test_preflight_compiles_python_files_that_are_not_imported(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td)
            (app / "config.py").write_text("APP_VERSION='1'\n", encoding="utf-8")
            (app / "unused.py").write_text("def broken(:\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unused.py"):
                updater._compile_python_tree(app)

    def test_verified_manifest_rejects_unlisted_file(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td)
            (app / "config.py").write_bytes(b"APP_VERSION='1'\n")
            (app / "unlisted.py").write_text("x=1", encoding="utf-8")
            (app / "version.json").write_text(json.dumps({
                "version": "1", "files": {"config.py": digest((app / "config.py").read_bytes())},
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "未列入哈希清单"):
                updater._read_app_manifest(app, verify_hashes=True)

    def test_copy_failure_stops_before_live_app_is_renamed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app = root / "app"
            app.mkdir()
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as z:
                z.writestr("config.py", "APP_VERSION='2'")
                z.writestr("version.json", json.dumps({"runtime_fingerprint": "fp"}))
            report = {"manifest_ok": False, "manifest_error": "bad",
                      "added": ["config.py"], "modified": [], "deleted": []}
            with (mock.patch.object(updater, "APP_DIR", app),
                  mock.patch.object(updater, "BUNDLE_DIR", root),
                  mock.patch.object(updater, "_wait_pid_exit", return_value=True),
                  mock.patch.object(updater, "_writable", return_value=True),
                  mock.patch.object(updater, "_read_app_manifest",
                                    return_value={"version": "2", "files": {"config.py": "0" * 64},
                                                  "runtime_fingerprint": "fp"}),
                  mock.patch.object(updater, "_preflight_app", return_value=("2", {})),
                  mock.patch.object(updater, "local_modification_report", return_value=report),
                  mock.patch.object(updater, "_preserve_user_app", side_effect=OSError("disk full")),
                  mock.patch.object(updater, "_rename_retry") as rename,
                  mock.patch.object(updater, "_pyw"),
                  mock.patch.object(updater, "C") as config):
                config.APP_VERSION = "1"
                (root / "python").mkdir()
                (root / "python" / ".paperpiggy-runtime.sha256").write_text("fp", encoding="ascii")
                result = updater.apply(package, relaunch=False)
            self.assertFalse(result["ok"])
            self.assertIn("保全用户修改失败", result["error"])
            rename.assert_not_called()
            self.assertTrue(app.exists())

    def test_unconfirmed_rollback_keeps_failed_and_restored_directories(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app = root / "app"
            old = root / ".app.old-1"
            app.mkdir(); old.mkdir()
            (app / "new.py").write_text("x=2", encoding="utf-8")
            (old / "old.py").write_text("x=1", encoding="utf-8")
            with (mock.patch.object(updater, "APP_DIR", app),
                  mock.patch.object(updater, "BUNDLE_DIR", root),
                  mock.patch.object(updater, "_importable", side_effect=RuntimeError("cannot import"))):
                ok, msg = updater._confirmed_rollback(old, "1", app)
            self.assertFalse(ok)
            self.assertIn("回滚未确认成功", msg)
            self.assertTrue(app.exists())
            self.assertTrue(any(root.glob(".app.failed*")))


class ModelInstallIntegrityTests(unittest.TestCase):
    def _complete_model(self, root, name="model"):
        d = Path(root) / name
        d.mkdir(parents=True)
        for fn in MB.MODEL_FILES:
            (d / fn).write_bytes((fn + "\n").encode())
        return d

    def test_half_model_is_not_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "bge-m3-onnx"
            d.mkdir(); (d / "model_quantized.onnx").write_bytes(b"partial")
            with mock.patch.object(MB.C, "MODELS", root):
                self.assertFalse(MB.model_present("bge-m3-onnx"))

    def test_legacy_complete_model_without_marker_stays_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._complete_model(root, "bge-m3-onnx")
            with mock.patch.object(MB.C, "MODELS", root):
                self.assertTrue(MB.model_present("bge-m3-onnx"))

    def test_tar_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                ti = tarfile.TarInfo("../escape")
                ti.size = 1
                tf.addfile(ti, io.BytesIO(b"x"))
            with self.assertRaisesRegex(RuntimeError, "不安全"):
                MB._safe_extract_model(archive, root / "stage", "model", archive.stat().st_size)
            self.assertFalse((root / "escape").exists())

    def test_tar_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "link.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                ti = tarfile.TarInfo("model/link")
                ti.type = tarfile.SYMTYPE
                ti.linkname = "../../escape"
                tf.addfile(ti)
            with self.assertRaisesRegex(RuntimeError, "链接"):
                MB._safe_extract_model(archive, root / "stage", "model", archive.stat().st_size)

    def test_complete_archive_is_staged_then_atomically_installed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self._complete_model(root / "source", "model")
            archive = root / "model.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                for p in source.iterdir():
                    tf.add(p, arcname=f"model/{p.name}")
            staged = MB._safe_extract_model(archive, root / "stage", "model", archive.stat().st_size)
            (staged / MB.READY_MARKER).write_text(json.dumps({
                "schema": 1, "name": "model", "archive_sha256": "a" * 64,
                "archive_bytes": archive.stat().st_size,
            }), encoding="utf-8")
            incomplete = root / "models" / "model"
            incomplete.mkdir(parents=True)
            (incomplete / "model_quantized.onnx").write_bytes(b"half")
            with mock.patch.object(MB.C, "MODELS", root / "models"):
                MB._install_staged_model(staged, "model")
                self.assertTrue(MB.model_present("model"))
            self.assertFalse(any((root / "models").glob(".model.incomplete-*")))


class BundleFreshnessTests(unittest.TestCase):
    def _fixture(self, root):
        bundle = root / "bundle"
        app = bundle / "app"
        py = bundle / "python"
        source = root / "source-config.py"
        app.mkdir(parents=True); py.mkdir()
        source.write_bytes(b"APP_VERSION='1'\n")
        (app / "config.py").write_bytes(source.read_bytes())
        fp = "f" * 64
        (app / "version.json").write_text(json.dumps({
            "version": "1", "runtime_fingerprint": fp,
            "files": {"config.py": digest(source.read_bytes())},
        }), encoding="utf-8")
        for fn in ("python.exe", "msvcp140.dll", "msvcp140_1.dll"):
            (py / fn).write_bytes(b"x")
        (py / ".paperpiggy-runtime.sha256").write_text(fp, encoding="ascii")
        runtime_src = root / "build" / "py312"
        runtime_src.mkdir(parents=True)
        (runtime_src / "python.exe").write_bytes(b"x")
        for name in BB.LEGAL_DOCS:
            (root / name).write_text(name, encoding="utf-8")
            (bundle / name).write_text(name, encoding="utf-8")
        return bundle, source

    def _patches(self, root, bundle, source):
        return (
            mock.patch.object(BI, "ROOT", root),
            mock.patch.object(BI, "BUNDLE", bundle),
            mock.patch.object(BI, "app_version", return_value="1"),
            mock.patch.object(BB, "source_app_files", return_value={"config.py": source}),
            mock.patch.object(BB, "_python_fingerprint", return_value="f" * 64),
        )

    def test_current_bundle_passes_integrity_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, source = self._fixture(root)
            patches = self._patches(root, bundle, source)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                BI.check_bundle()

    def test_stale_source_hash_aborts_packaging(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, source = self._fixture(root)
            source.write_bytes(b"APP_VERSION='1'\n# changed\n")
            patches = self._patches(root, bundle, source)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with self.assertRaisesRegex(SystemExit, "陈旧或损坏"):
                    BI.check_bundle()

    def test_app_data_aborts_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, source = self._fixture(root)
            bad = bundle / "app" / "data"
            bad.mkdir(); (bad / "secret.json").write_text("secret", encoding="utf-8")
            patches = self._patches(root, bundle, source)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with self.assertRaisesRegex(SystemExit, "没有自动删除"):
                    BI.check_bundle()
            self.assertTrue((bad / "secret.json").exists())

    def test_installer_explicitly_installs_legal_documents(self):
        iss = (ROOT / "installer" / "paperpiggy.iss").read_text(encoding="utf-8")
        self.assertIn('Source: "{#BundleDir}\\LICENSE"', iss)
        self.assertIn('Source: "{#BundleDir}\\THIRD-PARTY-NOTICES.md"', iss)
        notices = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
        self.assertNotIn("安装器 / 便携 zip", notices)


if __name__ == "__main__":
    unittest.main()
