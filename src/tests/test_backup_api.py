from contextlib import closing
import os
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import backup_api as api


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        environment = mock.patch.dict(os.environ, {'LOCALKB_DATA': str(self.data)})
        environment.start()
        self.addCleanup(environment.stop)
        patch = mock.patch.object(api.C, 'DATA', self.data)
        patch.start()
        self.addCleanup(patch.stop)
        self.server = types.SimpleNamespace(R=types.SimpleNamespace(), _backup_busy_reason=lambda: None)
        self.app = FastAPI()
        @self.app.post('/write')
        def write():
            return {'ok': True}
        api.register(self.app, self.server)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8771')

    def test_foreign_origin_and_simple_form_rejected(self):
        for headers, body in [
            ({'Origin': 'https://example.org'}, {}),
            ({'Sec-Fetch-Site': 'cross-site'}, {}),
            ({'Origin': 'http://127.0.0.1:8770'}, {}),
        ]:
            result = self.client.post('/backup2/create', json=body, headers=headers)
            self.assertEqual(403, result.status_code)
        self.assertEqual(403, self.client.post('/backup2/create', data='x=y').status_code)

    def test_missing_service_never_enqueues(self):
        with mock.patch.object(api.SERVICE, 'install_status', return_value={'installed': False}), \
                mock.patch.object(api.RUN, 'enqueue') as enqueue:
            result = self.client.post('/backup2/create', json={'kind': 'paperpiggy'}).json()
        self.assertFalse(result['ok'])
        enqueue.assert_not_called()

    def test_restore_requires_explicit_confirmation(self):
        with mock.patch.object(api, '_service_ready'), mock.patch.object(api, '_inspect') as inspect:
            result = self.client.post('/backup2/restore', json={
                'kind': 'paperpiggy', 'path': 'unused.zip', 'mode': 'in_place'}).json()
        self.assertFalse(result['ok'])
        self.assertIn('确认', result['error'])
        inspect.assert_not_called()

    def test_new_restore_refuses_nonempty_target(self):
        target = Path(self.temp.name) / 'occupied'
        target.mkdir()
        (target / 'valuable').write_text('keep')
        with mock.patch.object(api, '_service_ready'), \
                mock.patch.object(api, '_inspect', return_value={'manifest': {'kind': 'zotero', 'roots': []}}), \
                mock.patch.object(api.RUN, 'enqueue') as enqueue:
            result = self.client.post('/backup2/restore', json={
                'kind': 'zotero', 'path': 'unused.zip', 'mode': 'new',
                'destination': str(target), 'confirm': True}).json()
        self.assertFalse(result['ok'])
        self.assertEqual('keep', (target / 'valuable').read_text())
        enqueue.assert_not_called()

    def test_pending_in_place_restore_gates_writes(self):
        with mock.patch.object(api.RUN, 'active_paperpiggy_restore', return_value=True):
            self.assertEqual(503, self.client.post('/write', json={}).status_code)
            status = self.client.get('/backup2/status?kind=paperpiggy')
            self.assertEqual(200, status.status_code)

    def test_request_trigger_failure_keeps_identifiable_queue(self):
        with mock.patch.object(api.SERVICE, 'trigger_task', side_effect=OSError('temporarily busy')):
            result = api._queued({'request_id': 'a' * 32})
        self.assertTrue(result['ok'])
        self.assertTrue(result['queued'])
        self.assertEqual('a' * 32, result['request_id'])
        self.assertIn('temporarily busy', result['warning'])

    def test_configuration_rejects_unknown_fields(self):
        result = self.client.post('/backup2/config', json={
            'kind': 'paperpiggy', 'patch': {'machine_id': 'hijack'}}).json()
        self.assertFalse(result['ok'])


    def test_first_use_config_is_explicit_and_does_not_enable_empty_sources(self):
        service = {'installed': False, 'pending': False, 'schema': 1, 'error': ''}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=service), \
                mock.patch.object(api.SOURCES, 'discover_zotero', return_value={
                    'found': False, 'data_dir': '', 'profile_dir': '', 'candidates': []}), \
                mock.patch.object(api.SOURCES, 'discover_browser', return_value={'configured': False}):
            result = self.client.get('/backup2/config').json()
        self.assertTrue(result['ok'])
        self.assertEqual(service, result['service'])
        jobs = {row['kind']: row for row in result['jobs']}
        self.assertEqual({'paperpiggy', 'zotero'}, set(jobs))
        self.assertEqual(2, jobs['zotero']['keep'])
        self.assertTrue(jobs['paperpiggy']['include_browser'])
        for row in jobs.values():
            self.assertEqual(row['job_id'], row['id'])
            self.assertFalse(row['enabled'])
            self.assertEqual('', row['destination'])
            self.assertIn('running', row['status'])
        self.assertEqual('', result['discovered']['zotero']['profile_dir'])

    def test_empty_zotero_sources_can_be_saved_disabled_but_cannot_run(self):
        saved = self.client.post('/backup2/config', json={'kind': 'zotero', 'patch': {
            'enabled': False, 'destination': '', 'sources': {}}}).json()
        self.assertTrue(saved['ok'])
        with mock.patch.object(api, '_service_ready'), mock.patch.object(api.RUN, 'enqueue') as enqueue:
            started = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
        self.assertFalse(started['ok'])
        self.assertIn('Zotero', started['error'])
        enqueue.assert_not_called()

    def test_native_component_needing_update_refuses_create_and_enable(self):
        service = {'installed': True, 'needs_update': True, 'pending': False, 'schema': 1}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=service), \
                mock.patch.object(api.RUN, 'enqueue') as enqueue:
            created = self.client.post('/backup2/create', json={'kind': 'paperpiggy'}).json()
            enabled = self.client.post('/backup2/config', json={
                'kind': 'zotero', 'patch': {'enabled': True}}).json()
        self.assertFalse(created['ok'])
        self.assertFalse(enabled['ok'])
        self.assertIn('更新', created['error'])
        enqueue.assert_not_called()

    def test_in_place_restore_rejects_partial_root_mapping(self):
        manifest = {'kind': 'zotero', 'roots': [
            {'id': 'zotero_data', 'original_source': str(self.data / 'library')},
            {'id': 'zotero_profile', 'original_source': str(self.data / 'profile')}]}
        with mock.patch.object(api, '_service_ready'), \
                mock.patch.object(api, '_inspect', return_value={'manifest': manifest}), \
                mock.patch.object(api.RUN, 'enqueue') as enqueue:
            result = self.client.post('/backup2/restore', json={
                'kind': 'zotero', 'path': 'unused.zip', 'mode': 'in_place', 'confirm': True,
                'targets': {'zotero_data': str(self.data / 'library')}}).json()
        self.assertFalse(result['ok'])
        self.assertIn('全部来源', result['error'])
        enqueue.assert_not_called()

    def test_new_and_legacy_paperpiggy_restore_wait_for_exit(self):
        roots = [{'id': 'paperpiggy_data', 'original_source': str(self.data / 'foreign-data')},
                 {'id': 'paperpiggy_home', 'original_source': str(self.data / 'foreign-home')}]
        for legacy in (False, True):
            with self.subTest(legacy=legacy), mock.patch.object(api, '_service_ready'), \
                    mock.patch.object(api, '_inspect', return_value={
                        'legacy': legacy, 'manifest': {'kind': 'paperpiggy', 'roots': roots}}), \
                    mock.patch.object(api.SOURCES, 'ensure_restore_stopped') as stopped, \
                    mock.patch.object(api.SOURCES, 'ensure_paperpiggy_mcp_stopped') as mcp, \
                    mock.patch.object(api.RUN, '_version_warnings', return_value=[]), \
                    mock.patch.object(api.RUN, 'enqueue', return_value={'request_id': 'a' * 32}) as enqueue, \
                    mock.patch.object(api.SERVICE, 'trigger_task'):
                result = self.client.post('/backup2/restore', json={
                    'kind': 'paperpiggy', 'path': 'unused.zip', 'mode': 'in_place',
                    'confirm': True}).json()
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['requires_exit'])
                self.assertTrue(result['queued'])
                options = enqueue.call_args.args[2]
                self.assertEqual(os.getpid(), options['wait_for_pid'])
                self.assertEqual(str(api.C.APP / 'server.py'), options['wait_for_script'])
                if legacy:
                    self.assertEqual({'paperpiggy_data': str(self.data),
                                      'paperpiggy_home': str(self.data.parent)}, options['targets'])
                stopped.assert_called_once()
                mcp.assert_called_once_with(api.C.APP)

    def test_active_mcp_or_retrieval_refuses_paperpiggy_restore(self):
        manifest = {'kind': 'paperpiggy', 'roots': [
            {'id': 'paperpiggy_data', 'original_source': str(self.data)}]}
        for active in ('mcp', 'retrieval'):
            with self.subTest(active=active), mock.patch.object(api, '_service_ready'), \
                    mock.patch.object(api, '_inspect', return_value={'manifest': manifest}), \
                    mock.patch.object(api.SOURCES, 'ensure_restore_stopped'), \
                    mock.patch.object(api.RUN, '_version_warnings', return_value=[]), \
                    mock.patch.object(api.SOURCES, 'ensure_paperpiggy_mcp_stopped',
                                      side_effect=ValueError('MCP still connected') if active == 'mcp' else None), \
                    mock.patch.object(api.RUN, 'enqueue') as enqueue:
                self.server.R.retrieval_status = lambda: {'active': int(active == 'retrieval')}
                result = self.client.post('/backup2/restore', json={
                    'kind': 'paperpiggy', 'path': 'unused.zip', 'mode': 'in_place',
                    'confirm': True}).json()
                self.assertFalse(result['ok'])
                enqueue.assert_not_called()

    def test_status_history_and_list_preserve_ui_fields(self):
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        current.set_status(job['job_id'], operation='restore', request_id='a' * 32)
        current.finish(job['job_id'], 'failed', 'database invalid',
                       result={'warnings': ['keep old'], 'rollback_dirs': ['safe-rollback']})
        result = self.client.get('/backup2/status?kind=zotero').json()
        self.assertTrue(result['ok'])
        self.assertEqual('failed', result['state'])
        self.assertEqual('restore', result['operation'])
        self.assertFalse(result['cancellable'])
        self.assertEqual('database invalid', result['error'])
        self.assertIn('at', result['history'][-1])
        self.assertEqual('failed', result['history'][-1]['state'])
        self.assertEqual(['safe-rollback'], result['result']['rollback_dirs'])
        missing = self.data / 'missing.zip'
        with mock.patch.object(current, 'ledger', return_value=[{
                'path': str(missing), 'created_at': '2026-10-30', 'include_browser': True}]), \
                mock.patch.object(api, '_context', return_value=(current, job)):
            listing = self.client.get('/backup2/list?kind=zotero').json()
        self.assertTrue(listing['ok'])
        self.assertFalse(listing['items'][0]['available'])
        self.assertTrue(listing['items'][0]['include_browser'])

    def test_new_restore_covers_all_zotero_roots_and_keeps_sync_settings(self):
        import backup_archive
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        fixture = Path(self.temp.name) / 'static-source'
        library, profile, registry = [fixture / name for name in ('library', 'profile', 'registry')]
        for directory in (library, profile, registry):
            directory.mkdir(parents=True)
        with closing(sqlite3.connect(library / 'zotero.sqlite')) as db:
            db.execute('CREATE TABLE notes(value TEXT)')
            db.execute("INSERT INTO notes VALUES ('original note')")
            db.commit()
        (profile / 'prefs.js').write_text(
            'user_pref("sync.autoSync", true);\nuser_pref("plugin.setting", "keep");\n',
            encoding='utf-8')
        (profile / 'extensions').mkdir()
        (profile / 'extensions/plugin.xpi').write_bytes(b'fixture plugin')
        (registry / 'profiles.ini').write_text(
            '[Profile0]\nName=fixture\nIsRelative=0\nPath=' + str(profile) + '\n', encoding='utf-8')
        destination = Path(self.temp.name) / 'packages'
        job = current.save(job['job_id'], {'destination': str(destination)})
        created = backup_archive.create(job, [
            {'id': 'zotero_data', 'source': str(library)},
            {'id': 'zotero_profile', 'source': str(profile)},
            {'id': 'zotero_registry', 'source': str(registry), 'include': ['profiles.ini']}])
        restored = Path(self.temp.name) / 'restored'
        with mock.patch.object(api, '_service_ready'), mock.patch.object(api.SERVICE, 'trigger_task'):
            result = self.client.post('/backup2/restore', json={
                'kind': 'zotero', 'path': created['path'], 'mode': 'new',
                'destination': str(restored), 'confirm': True}).json()
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['requires_exit'])
        api.RUN.process_requests(current)
        status = current.status(job['job_id'])
        self.assertEqual('success', status['state'], status)
        for root in ('zotero_data', 'zotero_profile', 'zotero_registry'):
            self.assertTrue((restored / root).is_dir())
        prefs = (restored / 'zotero_profile/prefs.js').read_text(encoding='utf-8')
        self.assertIn('user_pref("sync.autoSync", true);', prefs)
        self.assertIn('user_pref("plugin.setting", "keep");', prefs)
        self.assertIn(str(restored / 'zotero_data').replace('\\', '\\\\'), prefs)
        self.assertEqual(b'fixture plugin', (restored / 'zotero_profile/extensions/plugin.xpi').read_bytes())


    def test_queued_backup_cancel_is_persisted_but_restore_cannot_cancel(self):
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        current.set_status(job['job_id'], running=True, pending=True, state='queued',
                           operation='backup', cancel_requested=False)
        queued = self.client.get('/backup2/status?kind=zotero').json()
        self.assertTrue(queued['cancellable'])
        result = self.client.post('/backup2/cancel', json={'kind': 'zotero'}).json()
        self.assertTrue(result['ok'])
        self.assertTrue(current.status(job['job_id'])['cancel_requested'])
        current.set_status(job['job_id'], operation='restore', cancel_requested=False)
        result = self.client.post('/backup2/cancel', json={'kind': 'zotero'}).json()
        self.assertFalse(result['ok'])
        self.assertFalse(current.status(job['job_id'])['cancel_requested'])


    def test_http_queued_cancel_finishes_without_starting_backup(self):
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        with mock.patch.object(api, '_service_ready'), \
                mock.patch.object(api.SOURCES, 'validate_sources'), \
                mock.patch.object(api.SERVICE, 'trigger_task'):
            created = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
        self.assertTrue(created['queued'])
        self.assertTrue(self.client.post('/backup2/cancel', json={'kind': 'zotero'}).json()['ok'])
        with mock.patch.object(api.RUN, 'run_backup') as run_backup:
            api.RUN.process_requests(current)
        run_backup.assert_not_called()
        status = self.client.get('/backup2/status?kind=zotero').json()
        self.assertEqual('cancelled', status['state'])
        self.assertFalse(status['running'])
        self.assertFalse(status['pending'])
        self.assertEqual(created['request_id'], status['request_id'])
        self.assertEqual([], current.ledger(job['job_id']))
        self.assertFalse((current.base / 'requests' / (created['request_id'] + '.json')).exists())


    def test_other_machine_tasks_are_preserved_but_local_api_remains_usable(self):
        from backup_jobs import JobStore, atomic_json
        foreign = JobStore(self.data / 'backup_jobs', machine_id='previous-machine')
        old_jobs = foreign.ensure_defaults()
        saved_files = {}
        for job in old_jobs:
            foreign.set_status(job['job_id'], running=True, operation='backup',
                               state='running', last_success='2026-01-01T07:00:00+08:00')
            ledger_path = foreign.base / 'ledger' / (job['job_id'] + '.json')
            atomic_json(ledger_path, [{'backup_id': 'f' * 32, 'path': 'previous-machine-backup.zip'}])
            for path in (ledger_path, foreign.base / 'status' / (job['job_id'] + '.json')):
                saved_files[path] = path.read_bytes()
        original_archive = self.data.parent / 'previous-machine-backup.zip'
        original_archive.write_bytes(b'untouched previous backup')
        with mock.patch.object(api.SERVICE, 'install_status', return_value={'installed': False, 'schema': 1}), \
                mock.patch.object(api.SOURCES, 'discover_zotero', return_value={'found': False}), \
                mock.patch.object(api.SOURCES, 'discover_browser', return_value={'configured': False}):
            result = self.client.get('/backup2/config').json()
        self.assertTrue(result['ok'], result)
        self.assertEqual(2, result['foreign_jobs_count'])
        self.assertIn('重新选择', result['notice'])
        self.assertEqual({'paperpiggy', 'zotero'}, {job['kind'] for job in result['jobs']})
        self.assertEqual(2, len(result['jobs']))
        self.assertTrue(all(job['machine_id'] != 'previous-machine' for job in result['jobs']))
        self.assertTrue(all(not job['enabled'] and not job['destination'] for job in result['jobs']))
        local_pp = next(job for job in result['jobs'] if job['kind'] == 'paperpiggy')
        changed = self.client.post('/backup2/config', json={'kind': 'paperpiggy', 'patch': {
            'destination': str(self.data.parent / 'new-machine-packages'), 'enabled': False}}).json()
        self.assertTrue(changed['ok'], changed)
        self.assertEqual(local_pp['job_id'], changed['job']['job_id'])
        self.assertNotEqual('previous-machine', changed['job']['machine_id'])
        status = self.client.get('/backup2/status?kind=paperpiggy').json()
        self.assertTrue(status['ok'], status)
        self.assertFalse(status['running'])
        current = api.RUN.store(self.data)
        self.assertEqual(4, len(current.list()))
        for original in old_jobs:
            self.assertEqual(original, current.get(original['job_id']))
        for path, content in saved_files.items():
            self.assertEqual(content, path.read_bytes())
        self.assertEqual(b'untouched previous backup', original_archive.read_bytes())


    def test_auto_component_check_queues_without_admin_and_keeps_old_runner_available(self):
        old = {'installed': True, 'needs_update': True, 'auto_update_supported': True,
               'migration_required': False, 'can_run': True, 'auto_update': {'state': 'idle'}}
        pending = {**old, 'auto_update': {'state': 'pending', 'pending': True}}
        with mock.patch.object(api.SERVICE, 'install_status', side_effect=[old, pending]), \
                mock.patch.object(api.SERVICE, 'request_component_update', create=True,
                                  return_value={'ok': True, 'pending': True}) as update, \
                mock.patch.object(api.SERVICE, 'request_install') as admin:
            result = api._component_status()
        update.assert_called_once_with(self.data)
        admin.assert_not_called()
        self.assertTrue(result['can_run'])
        self.assertEqual('pending', result['auto_update']['state'])

    def test_old_mode_config_never_triggers_admin_or_auto_migration(self):
        old = {'installed': True, 'needs_update': True, 'migration_required': True,
               'auto_update_supported': False, 'can_run': False}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=old), \
                mock.patch.object(api.SERVICE, 'request_component_update', create=True) as update, \
                mock.patch.object(api.SERVICE, 'request_install') as admin, \
                mock.patch.object(api.SOURCES, 'discover_zotero', return_value={}), \
                mock.patch.object(api.SOURCES, 'discover_browser', return_value={}):
            result = self.client.get('/backup2/config').json()
            retry = self.client.post('/backup2/component/update', json={}).json()
        self.assertTrue(result['ok'])
        self.assertTrue(result['service']['migration_required'])
        self.assertFalse(retry['ok'])
        self.assertIn('管理员', retry['error'])
        update.assert_not_called()
        admin.assert_not_called()

    def test_auto_update_failure_does_not_fail_a_compatible_backup(self):
        available = {'installed': True, 'needs_update': True, 'can_run': True,
                     'auto_update_supported': True,
                     'auto_update': {'state': 'failed', 'error': 'signature rejected'}}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=available), \
                mock.patch.object(api.SERVICE, 'request_component_update', create=True,
                                  side_effect=ValueError('signature rejected')), \
                mock.patch.object(api.SERVICE, 'request_install') as admin, \
                mock.patch.object(api.SOURCES, 'validate_sources'), \
                mock.patch.object(api.RUN, 'enqueue', return_value={'request_id': 'a' * 32}) as enqueue, \
                mock.patch.object(api.SERVICE, 'trigger_task'):
            state = api._service_ready()
            result = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
        self.assertTrue(state['can_run'])
        self.assertEqual('signature rejected', state['auto_update']['error'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['queued'])
        enqueue.assert_called_once()
        admin.assert_not_called()

    def test_component_retry_uses_fixed_channel_and_ignores_client_paths(self):
        state = {'installed': True, 'can_run': True, 'auto_update_supported': True,
                 'auto_update': {'state': 'failed', 'error': 'old failure'}}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=state), \
                mock.patch.object(api.SERVICE, 'request_component_update', create=True,
                                  return_value={'ok': True, 'state': 'pending', 'pending': True,
                                                'request_id': 'a' * 32}) as update, \
                mock.patch.object(api.SERVICE, 'request_install') as admin:
            result = self.client.post('/backup2/component/update', json={
                'helper_path': 'untrusted.exe', 'envelope_path': 'untrusted.json'}).json()
        self.assertTrue(result['ok'], result)
        update.assert_called_once_with(self.data, retry=True)
        admin.assert_not_called()
        self.assertTrue(result['service']['can_run'])

    def test_startup_component_check_is_background_only_and_respects_restore_gate(self):
        startup = next(handler for handler in self.app.router.on_startup
                       if handler.__name__ == 'component_update_startup')
        with mock.patch.object(api.RUN, 'active_paperpiggy_restore', return_value=False), \
                mock.patch.object(api.threading, 'Thread') as thread, \
                mock.patch.object(api.SERVICE, 'request_install') as admin:
            startup()
        self.assertTrue(thread.call_args.kwargs['daemon'])
        self.assertIs(api._component_status, thread.call_args.kwargs['target'])
        thread.return_value.start.assert_called_once()
        admin.assert_not_called()
        with mock.patch.object(api.RUN, 'active_paperpiggy_restore', return_value=True), \
                mock.patch.object(api.threading, 'Thread') as thread:
            startup()
        thread.assert_not_called()


    def test_opening_settings_automatically_queues_only_the_signed_channel(self):
        state = {'installed': True, 'needs_update': True, 'can_run': True,
                 'auto_update_supported': True, 'migration_required': False,
                 'auto_update': {'state': 'pending', 'pending': True}}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=state), \
                mock.patch.object(api.SERVICE, 'request_component_update', create=True,
                                  return_value={'ok': True, 'pending': True}) as update, \
                mock.patch.object(api.SERVICE, 'request_install') as admin, \
                mock.patch.object(api.SOURCES, 'discover_zotero', return_value={}), \
                mock.patch.object(api.SOURCES, 'discover_browser', return_value={}):
            result = self.client.get('/backup2/config').json()
        self.assertTrue(result['ok'])
        self.assertTrue(result['service']['can_run'])
        update.assert_called_once_with(self.data)
        admin.assert_not_called()


    def test_old_migration_marker_is_read_only_and_superseded_by_auto_current(self):
        marker = self.data / 'backup_work/component-install.json'
        marker.parent.mkdir()
        marker.write_text(json.dumps({'started_at': 100.0, 'payload_id': 'old-native-generation'}),
                          encoding='utf-8')
        original_bytes = marker.read_bytes()
        original = {'installed': True, 'installed_at': 200.0, 'pending': True,
                    'error': '后台组件更新未完成，请重新授权安装。',
                    'can_run': True, 'needs_update': False, 'auto_update_supported': True,
                    'auto_update': {'state': 'success', 'error': ''}}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=original):
            result = api._service_ready()
        self.assertTrue(result['installed'])
        self.assertTrue(result['can_run'])
        self.assertFalse(result['pending'])
        self.assertEqual('', result['error'])
        self.assertEqual(original['auto_update'], result['auto_update'])
        self.assertTrue(original['pending'])
        self.assertNotEqual('', original['error'])
        self.assertEqual(original_bytes, marker.read_bytes())

    def test_fresh_failed_marker_or_unverified_registration_is_not_hidden(self):
        marker = self.data / 'backup_work/component-install.json'
        marker.parent.mkdir()
        original = {'installed': True, 'installed_at': 200.0, 'pending': True,
                    'error': '当前新安装超时或失败', 'can_run': True, 'needs_update': False,
                    'auto_update_supported': True,
                    'auto_update': {'state': 'failed', 'error': 'separate update failure'}}
        for pending_at, installed, installed_at in [
                (201.0, True, 200.0), (float('nan'), True, 200.0),
                (float('inf'), True, 200.0), (-1, True, 200.0), (True, True, 200.0),
                (100.0, False, 200.0), (100.0, True, float('inf')),
                (100.0, True, 'invalid-time')]:
            with self.subTest(pending_at=pending_at, installed=installed, installed_at=installed_at):
                marker.write_text(json.dumps({'started_at': pending_at}), encoding='utf-8')
                saved = marker.read_bytes()
                state = {**original, 'installed': installed, 'installed_at': installed_at}
                with mock.patch.object(api.SERVICE, 'install_status', return_value=state):
                    result = api._service_info()
                self.assertEqual(state, result)
                self.assertEqual('separate update failure', result['auto_update']['error'])
                self.assertEqual(saved, marker.read_bytes())


if __name__ == '__main__':
    unittest.main()
