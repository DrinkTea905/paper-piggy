import ctypes
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_vss as V


class FakeVss:
    def __init__(self, **kwargs):
        self.closed = 0
        self.volumes = None
        self.kwargs = kwargs

    def start(self, volumes):
        self.volumes = volumes
        return "the-set", [{"snapshot_id": str(i), "set_id": "the-set", "volume": name,
                            "device": f"\\\\?\\GLOBALROOT\\Device\\Shadow{i}", "created": 123}
                           for i, name in enumerate(volumes)]

    def close(self):
        self.closed += 1


class VssTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeVss()
        self.platform = patch.object(V.sys, "platform", "win32")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.volume = patch.object(V, "_volume", side_effect=lambda p:
            (("C:\\", "volume-c") if str(p).upper().startswith("C:") else ("D:\\", "volume-d")))
        self.volume.start()
        self.addCleanup(self.volume.stop)

    def context(self, roots=None):
        return V.SnapshotSet(roots or ["C:\\user\\profile", "D:\\library"],
                             _backend_factory=lambda **kwargs: self.fake)

    def test_one_set_contains_all_unique_source_volumes(self):
        with self.context(["C:\\user\\profile", "D:\\library", "D:\\browser"]) as value:
            self.assertEqual(value.set_id, "the-set")
            self.assertEqual(self.fake.volumes, ["volume-c", "volume-d"])
            self.assertEqual({row["set_id"] for row in value.snapshots}, {"the-set"})
        self.assertEqual(self.fake.closed, 1)

    def test_mapping_includes_relative_file_and_rejects_unrelated_path(self):
        with self.context() as value:
            mapped = str(value.map_path("D:\\library\\storage\\file.pdf"))
            self.assertIn("Shadow1", mapped)
            self.assertTrue(mapped.endswith("library\\storage\\file.pdf"))
            with self.assertRaisesRegex(V.VssError, "不属于"):
                value.map_path("D:\\library-other\\secret")
            with self.assertRaises(V.VssError):
                value.map_path("C:\\Windows\\system.ini")

    def test_partial_snapshot_set_cannot_be_used(self):
        self.fake.start = lambda volumes: ("the-set", [
            {"volume": "volume-c", "set_id": "the-set", "device": "shadow"}])
        with self.assertRaisesRegex(V.VssError, "未全部"):
            with self.context():
                self.fail("partial set exposed")
        self.assertEqual(self.fake.closed, 1)

    def test_other_set_is_rejected_and_own_backend_closed(self):
        self.fake.start = lambda volumes: ("the-set", [
            {"volume": v, "set_id": "someone-else", "device": "shadow"} for v in volumes])
        with self.assertRaises(V.VssError):
            with self.context():
                pass
        self.assertEqual(self.fake.closed, 1)

    def test_creation_failure_closes_only_current_backend(self):
        self.fake.start = Mock(side_effect=V.VssError("provider failed"))
        with self.assertRaisesRegex(V.VssError, "provider failed"):
            with self.context():
                pass
        self.assertEqual(self.fake.closed, 1)

    def test_cleanup_failure_is_not_silent_success(self):
        self.fake.close = Mock(side_effect=V.VssError("cleanup failed"))
        with self.assertRaisesRegex(V.VssError, "cleanup failed"):
            with self.context():
                pass

    def test_cleanup_does_not_hide_original_copy_error(self):
        self.fake.close = Mock(side_effect=V.VssError("cleanup failed"))
        with self.assertRaisesRegex(ValueError, "copy failed") as found:
            with self.context():
                raise ValueError("copy failed")
        self.assertIn("cleanup failed", " ".join(found.exception.__notes__))

    def test_non_windows_and_empty_sources_rejected(self):
        with patch.object(V.sys, "platform", "linux"):
            with self.assertRaisesRegex(V.VssError, "Windows"):
                with self.context():
                    pass
        with self.assertRaisesRegex(V.VssError, "没有"):
            V.SnapshotSet([])

    def test_access_denied_has_no_live_copy_fallback(self):
        shell = Mock()
        shell.IsUserAnAdmin.return_value = False
        with patch.object(V, "_native_privileges", return_value=nullcontext()), \
                patch.object(V.ctypes, "WinDLL", return_value=shell), \
                patch.object(V.ctypes, "OleDLL") as com:
            with self.assertRaisesRegex(V.VssError, "未复制活动数据库"):
                V._NativeVss().start(["volume-c"])
            com.assert_not_called()

    def test_guid_abi_and_hresult(self):
        identifier = uuid.uuid4()
        self.assertEqual(str(V.GUID.from_buffer_copy(identifier.bytes_le)), str(identifier))
        self.assertEqual(ctypes.sizeof(V.GUID), 16)
        with self.assertRaisesRegex(V.VssError, "管理员"):
            V._check(-2147024891, "snapshot")
        self.assertEqual(V._check(0x4230A, "snapshot"), 0x4230A)

    @unittest.skipUnless(sys.platform == "win32", "需要 Windows ABI / 系统 DLL")
    def test_windows_native_abi_and_required_exports_without_creating_snapshot(self):
        self.assertEqual(ctypes.sizeof(V.HRESULT), 4)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(V.SNAPSHOT_PROP), 128)
            self.assertEqual(V.SNAPSHOT_PROP.device.offset, 40)
            self.assertEqual(V.SNAPSHOT_PROP.created.offset, 112)
        api = ctypes.WinDLL("vssapi")
        self.assertTrue(api.CreateVssBackupComponentsInternal)
        self.assertTrue(api.VssFreeSnapshotPropertiesInternal)


if __name__ == "__main__":
    unittest.main()
