import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher
import backup_service
import backup_runner


class BackupNativeTests(unittest.TestCase):
    def test_remote_page_cannot_request_admin_component(self):
        window = mock.Mock()
        window.get_current_url.return_value = 'https://example.org/'
        with mock.patch.dict(sys.modules, {'webview': types.SimpleNamespace(windows=[window])}), \
                mock.patch.object(backup_service, 'install_payload') as payload:
            result = launcher._JsApi().install_backup_service()
        self.assertFalse(result['ok'])
        payload.assert_not_called()

    def test_component_payload_is_fixed_to_current_app(self):
        window = mock.Mock()
        window.get_current_url.return_value = launcher.C.DAEMON_URL + '/#backup'
        with mock.patch.dict(sys.modules, {'webview': types.SimpleNamespace(windows=[window])}), \
                mock.patch.object(backup_service, 'install_payload', return_value={'prepared': True}) as payload, \
                mock.patch.object(backup_service, 'request_install', return_value={'ok': True, 'pending': True}) as install:
            result = launcher._JsApi().install_backup_service()
        self.assertTrue(result['pending'])
        self.assertEqual(launcher.C.DATA, payload.call_args.args[0])
        self.assertEqual({launcher.C.APP / name for name in backup_service.MODULES},
                         set(payload.call_args.args[1]))
        install.assert_called_once_with({'prepared': True})

    def test_restore_close_without_pending_request_does_not_close(self):
        window = mock.Mock()
        window.get_current_url.return_value = launcher.C.DAEMON_URL
        with mock.patch.dict(sys.modules, {'webview': types.SimpleNamespace(windows=[window])}), \
                mock.patch.object(backup_runner, 'active_paperpiggy_restore', return_value=False):
            result = launcher._JsApi().close_for_backup_restore()
        self.assertFalse(result['ok'])
        window.destroy.assert_not_called()

    def test_launcher_restore_gate_precedes_server_start(self):
        with mock.patch.object(backup_runner, 'active_paperpiggy_restore', return_value=True), \
                mock.patch.object(launcher, '_notify') as notify, \
                mock.patch.object(launcher, '_health') as health, \
                mock.patch.object(launcher.subprocess, 'Popen') as popen:
            launcher.main()
        notify.assert_called_once()
        health.assert_not_called()
        popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
