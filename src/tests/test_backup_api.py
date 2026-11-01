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

    def test_listing_keeps_parts_and_index_as_one_lightweight_recovery_point(self):
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        folder = Path(self.temp.name) / 'packages'
        folder.mkdir()
        first, second, index = [folder / name for name in ('set-001.zip', 'set-002.zip', 'set.json')]
        for file, value in ((first, b'zip1'), (second, b'zip2'), (index, b'{}')):
            file.write_bytes(value)
        entry = {'path': str(first), 'archive_format': 3, 'part_count': 2,
                 'parts': [{'path': str(first), 'size': 4, 'sha256': 'a' * 64},
                           {'path': str(second), 'size': 4, 'sha256': 'b' * 64}],
                 'index_path': str(index), 'index_size': 2, 'total_size': 10, 'size': 10}
        with mock.patch.object(current, 'ledger', return_value=[entry]), \
                mock.patch.object(api, '_context', return_value=(current, job)), \
                mock.patch('backup_archive.inspect', side_effect=AssertionError('Listing must not inspect archives')), \
                mock.patch('backup_archive.file_sha256', side_effect=AssertionError('Listing must not hash archives')):
            result = self.client.get('/backup2/list?kind=zotero').json()
            self.assertTrue(result['ok'])
            self.assertEqual(1, len(result['items']))
            row = result['items'][0]
            self.assertTrue(row['available'])
            self.assertEqual(2, row['part_count'])
            self.assertEqual(10, row['total_size'])
            self.assertEqual(['set-001.zip', 'set-002.zip'], [x['name'] for x in row['parts']])
            self.assertTrue(row['index']['available'])
            for removed in (second, index):
                with self.subTest(missing=removed.name):
                    value = removed.read_bytes()
                    removed.unlink()
                    row = self.client.get('/backup2/list?kind=zotero').json()['items'][0]
                    self.assertFalse(row['available'])
                    self.assertIn(removed.name, row['missing_files'])
                    self.assertTrue(first.is_file())
                    self.assertFalse(removed.exists())
                    removed.write_bytes(value)
            second.write_bytes(b'changed-size')
            row = self.client.get('/backup2/list?kind=zotero').json()['items'][0]
            self.assertFalse(row['available'])
            self.assertIn('文件大小已变化', row['parts'][1]['problem'])

    def test_listing_old_zip_and_cleanup_pending_are_not_misrepresented(self):
        file = Path(self.temp.name) / 'old-format.zip'
        file.write_bytes(b'old data')
        row = api._listing_entry({'path': str(file), 'size': file.stat().st_size}, 'paperpiggy')
        self.assertTrue(row['available'])
        self.assertEqual(2, row['archive_format'])
        self.assertEqual(1, row['part_count'])
        self.assertIsNone(row['index'])
        pending = api._listing_entry({'path': str(file), 'size': file.stat().st_size,
                                      'cleanup_pending': True}, 'paperpiggy')
        self.assertFalse(pending['available'])
        self.assertTrue(pending['cleanup_pending'])
        self.assertIn('不作为可恢复版本', pending['availability_errors'][0])

    def test_listing_hides_retired_records_without_changing_task_state(self):
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        current.set_status(job['job_id'], state='success', operation='backup', running=False,
                           result={'path': 'latest.zip'})
        before = current.status(job['job_id'])
        present = Path(self.temp.name) / 'latest.zip'
        present.write_bytes(b'keep')
        rows = [{'path': str(Path(self.temp.name) / 'deleted.zip'), 'size': 10,
                 'retired': True, 'retired_reason': 'all_files_removed'},
                {'path': str(present), 'size': 4}]
        with mock.patch.object(current, 'ledger', return_value=rows), \
                mock.patch.object(api, '_context', return_value=(current, job)):
            result = self.client.get('/backup2/list?kind=zotero').json()
        self.assertTrue(result['ok'])
        self.assertEqual([str(present)], [item['path'] for item in result['items']])
        self.assertTrue(result['items'][0]['available'])
        self.assertEqual(before, current.status(job['job_id']))
        self.assertEqual(b'keep', present.read_bytes())
        self.assertEqual(2, len(rows))

    def test_listing_refuses_duplicate_or_out_of_directory_members(self):
        folder = Path(self.temp.name) / 'packages'
        folder.mkdir()
        first = folder / 'first.zip'
        index = folder / 'set.json'
        outside = Path(self.temp.name) / 'unrelated.zip'
        for file in (first, index, outside):
            file.write_bytes(b'data')
        for other in (first, outside):
            with self.subTest(other=str(other)):
                row = api._listing_entry({'path': str(first), 'archive_format': 3, 'part_count': 2,
                    'parts': [{'path': str(first), 'size': 4}, {'path': str(other), 'size': 4}],
                    'index_path': str(index), 'index_size': 4}, 'zotero')
                self.assertFalse(row['available'])

    def test_new_parts_inspection_failure_does_not_enqueue_restore(self):
        with mock.patch.object(api, '_service_ready'), \
                mock.patch.object(api, '_inspect', side_effect=ValueError('缺少分包：set-002.zip')), \
                mock.patch.object(api.RUN, 'enqueue') as enqueue:
            result = self.client.post('/backup2/restore', json={
                'kind': 'zotero', 'path': 'set-001.zip', 'mode': 'new',
                'destination': str(Path(self.temp.name) / 'restore'), 'confirm': True}).json()
        self.assertFalse(result['ok'])
        self.assertIn('set-002.zip', result['error'])
        enqueue.assert_not_called()

    def test_multipart_inspection_preserves_package_fields(self):
        import backup_archive
        import backup_legacy
        info = {'manifest': {'format': 2, 'kind': 'zotero', 'include_browser': False},
                'archive_format': 3, 'part_count': 2, 'total_size': 9,
                'index_path': 'set.json', 'parts': [{'path': 'set-001.zip', 'size': 4},
                                                  {'path': 'set-002.zip', 'size': 3}]}
        with mock.patch.object(backup_legacy, 'format_of', return_value=3), \
                mock.patch.object(backup_archive, 'inspect', return_value=info) as inspected:
            result = self.client.post('/backup2/inspect', json={'path': 'set-002.zip'}).json()
        self.assertTrue(result['ok'])
        self.assertFalse(result['legacy'])
        self.assertEqual(2, result['part_count'])
        self.assertEqual(9, result['total_size'])
        self.assertEqual('set.json', result['index_path'])
        inspected.assert_called_once_with('set-002.zip')

    def test_header_summary_accepts_lightweight_multipart_success(self):
        from backup_jobs import atomic_json
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, 'zotero')
        state = {'job_id': job['job_id'], 'operation': 'backup', 'state': 'success',
                 'running': False, 'finished_at': '2026-11-01T10:00:00+08:00',
                 'result': {'path': 'D:/fixture/first.zip', 'index_path': 'D:/fixture/set.json',
                            'archive_format': 3, 'part_count': 2, 'total_size': 8, 'index_size': 2}}
        status_path = current.base / 'status' / (job['job_id'] + '.json')
        atomic_json(status_path, state)
        response = self.client.get('/backup2/summary').json()
        self.assertTrue(response['ok'])
        item = next(row for row in response['jobs'] if row['kind'] == 'zotero')
        self.assertFalse(item['running'])
        self.assertIsNone(item['issue'])
        state['result']['part_count'] = 'two'
        atomic_json(status_path, state)
        response = self.client.get('/backup2/summary').json()
        self.assertFalse(response['ok'])

    def test_real_multipart_api_queue_listing_inspection_and_restore(self):
        """Exercise real packing/ledger/restore through HTTP using a static fixture snapshot."""
        import backup_archive as archive
        import backup_vss
        current = api.RUN.store(self.data)
        original = api.RUN.get_job(current, 'zotero')
        fixture = Path(self.temp.name) / 'static'
        library, profile = fixture / 'library', fixture / 'profile'
        library.mkdir(parents=True)
        profile.mkdir()
        payload = os.urandom(145000)
        (library / 'large.bin').write_bytes(payload)
        (profile / 'prefs.js').write_text('user_pref("sync.autoSync", true);', encoding='utf-8')
        with closing(sqlite3.connect(library / 'zotero.sqlite')) as db:
            db.execute('CREATE TABLE notes(value TEXT)')
            db.execute("INSERT INTO notes VALUES ('whole API restore')")
            db.commit()
        job = current.save(original['job_id'], {'destination': str(fixture / 'packages'),
                           'sources': {'data_dir': str(library), 'profile_dir': str(profile)}, 'keep': 2})
        roots = [{'id': 'zotero_data', 'source': str(library)},
                 {'id': 'zotero_profile', 'source': str(profile)}]
        old = archive._create_v2(job, roots)
        current.record_success(job['job_id'], old)
        old_row = self.client.get('/backup2/list?kind=zotero').json()['items'][0]
        self.assertTrue(old_row['available'])
        self.assertEqual(2, old_row['archive_format'])
        self.assertEqual(1, old_row['part_count'])

        class StaticSnapshot:
            set_id, snapshots = 'api-static-fixture', []
            def __init__(self, paths, cancel=None):
                self.paths = paths
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def map_path(self, path):
                return Path(path)

        create = archive.create
        with mock.patch.object(api, '_service_ready'), \
                mock.patch.object(api.SERVICE, 'trigger_task'), \
                mock.patch.object(api.SOURCES, 'validate_sources', return_value=(roots, {})), \
                mock.patch.object(api.SOURCES, 'validate_zotero_attachments', return_value=None), \
                mock.patch.object(backup_vss, 'snapshot', StaticSnapshot), \
                mock.patch.object(archive, 'create', side_effect=lambda *args, **kw: create(
                    *args, **kw, max_part_size=65536)):
            queued = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
            self.assertTrue(queued['ok'], queued)
            api.RUN.process_requests(current)
        state = current.status(job['job_id'])
        self.assertEqual('success', state['state'], state)
        listing = self.client.get('/backup2/list?kind=zotero').json()
        self.assertTrue(listing['ok'], listing)
        self.assertEqual(2, len(listing['items']))
        row = listing['items'][0]
        self.assertTrue(row['available'], row)
        self.assertEqual(3, row['archive_format'])
        self.assertGreater(row['part_count'], 1)
        self.assertTrue(row['index']['available'])
        self.assertTrue(all(p['size'] <= 65536 for p in row['parts']))
        self.assertGreater(row['total_size'], row['parts'][0]['size'])
        selected = row['parts'][-1]['path']
        checked = self.client.post('/backup2/inspect', json={'path': selected}).json()
        self.assertTrue(checked['ok'], checked)
        self.assertEqual(row['part_count'], checked['part_count'])
        self.assertEqual(row['index_path'], checked['index_path'])
        self.assertTrue(self.client.get('/backup2/summary').json()['ok'])

        target = fixture / 'restored'
        with mock.patch.object(api, '_service_ready'), mock.patch.object(api.SERVICE, 'trigger_task'):
            queued = self.client.post('/backup2/restore', json={'kind': 'zotero', 'path': selected,
                'mode': 'new', 'destination': str(target), 'confirm': True}).json()
        self.assertTrue(queued['ok'], queued)
        api.RUN.process_requests(current)
        self.assertEqual('success', current.status(job['job_id'])['state'])
        self.assertEqual(payload, (target / 'zotero_data/large.bin').read_bytes())
        with closing(sqlite3.connect(target / 'zotero_data/zotero.sqlite')) as db:
            self.assertEqual('whole API restore', db.execute('SELECT value FROM notes').fetchone()[0])
        self.assertIn('sync.autoSync", true', (target / 'zotero_profile/prefs.js').read_text())

        missing = Path(row['parts'][0]['path'])
        missing.unlink()
        listed = self.client.get('/backup2/list?kind=zotero').json()['items'][0]
        self.assertFalse(listed['available'])
        self.assertIn(missing.name, listed['missing_files'])
        broken = self.client.post('/backup2/inspect', json={'path': selected}).json()
        self.assertFalse(broken['ok'])
        self.assertIn(missing.name, broken['error'])
        untouched = fixture / 'must-not-restore'
        with mock.patch.object(api, '_service_ready'), mock.patch.object(api.RUN, 'enqueue') as enqueue:
            rejected = self.client.post('/backup2/restore', json={'kind': 'zotero', 'path': selected,
                'mode': 'new', 'destination': str(untouched), 'confirm': True}).json()
        self.assertFalse(rejected['ok'])
        enqueue.assert_not_called()
        self.assertFalse(untouched.exists())

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

    def test_pre_multipart_component_blocks_new_backup_and_enable_without_admin(self):
        old = {'installed': True, 'needs_update': False, 'auto_update_supported': True,
               'migration_required': False, 'can_run': True,
               'auto_update': {'state': 'pending', 'pending': True, 'component_version': 1001019000}}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=old), \
                mock.patch.object(api.SERVICE, 'request_component_update', return_value={'ok': True}) as update, \
                mock.patch.object(api.SERVICE, 'request_install') as admin, \
                mock.patch.object(api.RUN, 'enqueue') as enqueue, \
                mock.patch.object(api.SOURCES, 'validate_sources') as validate:
            service = api._service_info()
            created = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
            enabled = self.client.post('/backup2/config', json={
                'kind': 'zotero', 'patch': {'enabled': True}}).json()
        self.assertTrue(service['multipart_update_required'])
        self.assertFalse(service['can_run'])
        self.assertTrue(service['needs_update'])
        for reply in (created, enabled):
            self.assertFalse(reply['ok'], reply)
            self.assertIn('自动更新分包支持', reply['error'])
            self.assertIn('无需管理员', reply['error'])
            self.assertNotIn('重新授权', reply['error'])
        self.assertEqual(2, update.call_count)
        enqueue.assert_not_called()
        validate.assert_not_called()
        admin.assert_not_called()

    def test_multipart_capable_component_allows_backup(self):
        for version in (api.MULTIPART_MIN_COMPONENT_VERSION, api.MULTIPART_MIN_COMPONENT_VERSION + 1):
            with self.subTest(version=version):
                ready = {'installed': True, 'needs_update': False, 'can_run': True,
                         'auto_update_supported': True,
                         'auto_update': {'state': 'idle', 'component_version': version}}
                with mock.patch.object(api.SERVICE, 'install_status', return_value=ready), \
                        mock.patch.object(api.SERVICE, 'request_component_update') as update, \
                        mock.patch.object(api.SERVICE, 'request_install') as admin, \
                        mock.patch.object(api.SOURCES, 'validate_sources'), \
                        mock.patch.object(api.RUN, 'enqueue', return_value={'request_id': 'a' * 32}) as enqueue, \
                        mock.patch.object(api.SERVICE, 'trigger_task'):
                    service = api._service_info()
                    result = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
                self.assertFalse(service['multipart_update_required'])
                self.assertTrue(service['can_run'])
                self.assertTrue(result['ok'], result)
                enqueue.assert_called_once()
                update.assert_not_called()
                admin.assert_not_called()

    def test_pre_multipart_update_failure_offers_only_ordinary_retry(self):
        old = {'installed': True, 'needs_update': True, 'can_run': True,
               'auto_update_supported': True, 'migration_required': False,
               'auto_update': {'state': 'failed', 'error': 'signature rejected',
                               'component_version': 1001019000}}
        def request(data, retry=False):
            if not retry:
                raise ValueError('signature rejected')
            return {'ok': True, 'pending': True}
        with mock.patch.object(api.SERVICE, 'install_status', return_value=old), \
                mock.patch.object(api.SERVICE, 'request_component_update', side_effect=request) as update, \
                mock.patch.object(api.SERVICE, 'request_install') as admin, \
                mock.patch.object(api.RUN, 'enqueue') as enqueue:
            result = self.client.post('/backup2/create', json={'kind': 'zotero'}).json()
            retry = self.client.post('/backup2/component/update', json={}).json()
        self.assertFalse(result['ok'])
        self.assertIn('重试后台更新', result['error'])
        self.assertIn('无需管理员', result['error'])
        self.assertNotIn('重新授权', result['error'])
        self.assertTrue(retry['ok'])
        self.assertFalse(retry['service']['can_run'])
        self.assertEqual(mock.call(self.data, retry=True), update.call_args_list[-1])
        enqueue.assert_not_called()
        admin.assert_not_called()

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


class BackupSummaryTests(unittest.TestCase):
    setUp = BackupApiTests.setUp

    def fixture(self, kind='paperpiggy', history=None, **status):
        from backup_jobs import atomic_json
        current = api.RUN.store(self.data)
        job = api.RUN.get_job(current, kind)
        atomic_json(current.base / 'history' / (job['job_id'] + '.json'), history or [])
        if status:
            current.set_status(job['job_id'], **status)
        return current, job

    def summary(self):
        result = self.client.get('/backup2/summary').json()
        self.assertTrue(result['ok'], result)
        return {job['kind']: job for job in result['jobs']}

    def snapshot(self):
        return {str(path.relative_to(self.data)): path.read_bytes()
                for path in self.data.rglob('*') if path.is_file()}

    def event(self, state, at='2026-10-30T08:00:00+08:00', **fields):
        return {'state': state, 'at': at, **fields}

    def test_empty_summary_does_not_initialize_or_discover(self):
        before = self.snapshot()
        with mock.patch.object(api.RUN, 'store', side_effect=AssertionError('must not initialize')), \
                mock.patch.object(api.SERVICE, 'install_status', side_effect=AssertionError('must not inspect component')), \
                mock.patch.object(api.SOURCES, 'discover_zotero', side_effect=AssertionError('must not discover')), \
                mock.patch.object(api.SOURCES, 'discover_browser', side_effect=AssertionError('must not discover')):
            self.assertEqual({}, self.summary())
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], list(self.data.iterdir()))

    def test_summary_is_byte_for_byte_read_only_and_bypasses_scheduler(self):
        from backup_jobs import JobStore
        current, job = self.fixture(history=[self.event('failed', error='volume offline')],
                                    operation='backup', state='failed', error='volume offline',
                                    finished_at='2026-10-30T08:00:00+08:00')
        before = self.snapshot()
        directories = sorted(str(path) for path in self.data.rglob('*') if path.is_dir())
        with mock.patch.object(JobStore, 'due', side_effect=AssertionError('must not schedule')), \
                mock.patch.object(JobStore, 'ensure_defaults', side_effect=AssertionError('must not initialize')), \
                mock.patch.object(api, '_component_status', side_effect=AssertionError('must not update')), \
                mock.patch.object(api.SOURCES, 'validate_sources', side_effect=AssertionError('must not inspect sources')):
            result = self.summary()['paperpiggy']
        self.assertEqual('failed', result['issue']['level'])
        self.assertEqual('volume offline', result['issue']['message'])
        self.assertEqual('backup', result['issue']['operation'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(directories, sorted(str(path) for path in self.data.rglob('*') if path.is_dir()))

    def test_retry_and_cancellation_keep_failure_visible_with_same_id(self):
        failed = [self.event('started', trigger='timer'), self.event('failed', error='snapshot failed')]
        current, job = self.fixture(history=failed)
        initial = self.summary()['paperpiggy']['issue']
        current.set_status(job['job_id'], running=True, pending=True, state='queued', operation='backup', error='')
        self.assertEqual(initial, self.summary()['paperpiggy']['issue'])
        current.finish(job['job_id'], 'cancelled', 'user stopped')
        self.assertEqual(initial, self.summary()['paperpiggy']['issue'])

    def test_restore_success_does_not_dismiss_backup_failure(self):
        history = [self.event('started', trigger='manual'), self.event('failed', error='backup failed'),
                   self.event('started', trigger='restore'),
                   self.event('success', result={'destination': 'restore-target'})]
        self.fixture(history=history)
        issue = self.summary()['paperpiggy']['issue']
        self.assertEqual('backup failed', issue['message'])
        self.assertEqual('backup', issue['operation'])

    def test_successful_backup_clears_failure_but_not_later_restore_failure(self):
        history = [self.event('started', trigger='login'), self.event('failed', error='old failure'),
                   self.event('started', trigger='manual'),
                   self.event('success', result={'path': 'new.zip'})]
        current, job = self.fixture(history=history)
        self.assertIsNone(self.summary()['paperpiggy']['issue'])
        current._event(job['job_id'], self.event('started', trigger='restore'))
        current._event(job['job_id'], self.event('failed', error='restore failed'))
        issue = self.summary()['paperpiggy']['issue']
        self.assertEqual('restore', issue['operation'])
        self.assertEqual('restore failed', issue['message'])
        # A later successful backup also cannot prove that a failed restore was repaired.
        current._event(job['job_id'], self.event('success', result={'path': 'later.zip'}))
        self.assertEqual(issue, self.summary()['paperpiggy']['issue'])

    def test_success_warning_is_yellow_and_source_missing_count_is_not_warning(self):
        history = [self.event('started', trigger='timer'), self.event('failed', error='old failed'),
                   self.event('success_with_warning', result={'path': 'good.zip', 'warnings': ['old cleanup failed']})]
        current, job = self.fixture(history=history)
        issue = self.summary()['paperpiggy']['issue']
        self.assertEqual('warning', issue['level'])
        self.assertEqual('old cleanup failed', issue['message'])
        current._event(job['job_id'], self.event('success', result={'path': 'new.zip',
                           'source_coverage': {'missing': 93, 'existing': 100}}))
        self.assertIsNone(self.summary()['paperpiggy']['issue'])

    def test_two_jobs_and_foreign_machine_are_independent(self):
        from backup_jobs import JobStore
        foreign = JobStore(self.data / 'backup_jobs', machine_id='other-machine')
        foreign.ensure_defaults()
        self.fixture('paperpiggy', history=[self.event('failed', error='local failure')])
        self.fixture('zotero', running=True, operation='backup', stage='copying')
        jobs = self.summary()
        self.assertEqual({'paperpiggy', 'zotero'}, set(jobs))
        self.assertEqual('local failure', jobs['paperpiggy']['issue']['message'])
        self.assertTrue(jobs['zotero']['running'])
        self.assertIsNone(jobs['zotero']['issue'])

    def test_unknown_legacy_failure_is_not_cleared_by_unknown_or_restore_success(self):
        history = [self.event('failed', error='legacy failure'), self.event('success'),
                   self.event('success', result={'journal': 'restore-journal.json'})]
        current, job = self.fixture(history=history)
        self.assertEqual('unknown', self.summary()['paperpiggy']['issue']['operation'])
        current._event(job['job_id'], self.event('success', result={'path': 'backup.zip'}))
        self.assertIsNone(self.summary()['paperpiggy']['issue'])

    def test_finish_status_before_history_is_included_without_writing(self):
        self.fixture(history=[self.event('started', trigger='manual')],
                     state='failed', operation='backup', running=False, error='disk full',
                     finished_at='2026-10-30T08:01:00+08:00')
        before = self.snapshot()
        self.assertEqual('disk full', self.summary()['paperpiggy']['issue']['message'])
        self.assertEqual(before, self.snapshot())

    def test_finish_success_window_clears_old_failure_using_instant_not_offset_string(self):
        self.fixture(history=[self.event('failed', at='2026-10-30T08:00:00+08:00', error='old')],
                     state='success', operation='backup', running=False, result={'path': 'new.zip'},
                     finished_at='2026-10-30T00:01:00+00:00')
        self.assertIsNone(self.summary()['paperpiggy']['issue'])

    def test_finish_history_written_next_second_does_not_create_second_failure_identity(self):
        current, job = self.fixture(history=[self.event('started', trigger='manual')],
                                    state='failed', operation='backup', running=False,
                                    error='disk full', finished_at='2026-10-30T08:00:00+08:00')
        issue = self.summary()['paperpiggy']['issue']
        current._event(job['job_id'], self.event('failed', at='2026-10-30T08:00:01+08:00',
                                                error='disk full', result={}))
        self.assertEqual(issue, self.summary()['paperpiggy']['issue'])

    def test_cross_second_failure_identity_survives_queue_and_running_retry(self):
        current, job = self.fixture(history=[self.event('started', trigger='manual')],
                                    state='failed', operation='backup', running=False,
                                    error='disk full', finished_at='2026-10-30T08:00:00+08:00')
        original_id = self.summary()['paperpiggy']['issue']['id']
        current._event(job['job_id'], self.event('failed', at='2026-10-30T08:00:01+08:00',
                                                error='disk full', result={}))
        self.assertEqual(original_id, self.summary()['paperpiggy']['issue']['id'])
        current.set_status(job['job_id'], state='queued', running=True, pending=True,
                           operation='backup', error='', result={})
        self.assertEqual(original_id, self.summary()['paperpiggy']['issue']['id'])
        current._event(job['job_id'], self.event('started', at='2026-10-30T08:01:00+08:00', trigger='manual'))
        current.set_status(job['job_id'], state='running', pending=False, running=True,
                           started_at='2026-10-30T08:01:00+08:00')
        self.assertEqual(original_id, self.summary()['paperpiggy']['issue']['id'])
        current.finish(job['job_id'], 'failed', 'disk full')
        self.assertNotEqual(original_id, self.summary()['paperpiggy']['issue']['id'])

    def test_claim_and_request_worker_duplicate_failure_share_attempt_identity(self):
        current, job = self.fixture(history=[self.event('started', trigger='timer'),
                                              self.event('failed', error='snapshot failed', result={})],
                                    state='failed', operation='backup', running=False,
                                    error='snapshot failed', result={},
                                    finished_at='2026-10-30T08:00:00+08:00')
        original_id = self.summary()['paperpiggy']['issue']['id']
        current._event(job['job_id'], self.event('failed', at='2026-10-30T08:00:01+08:00',
                                                error='snapshot failed', result={}))
        current.set_status(job['job_id'], finished_at='2026-10-30T08:00:01+08:00')
        self.assertEqual(original_id, self.summary()['paperpiggy']['issue']['id'])
        current.set_status(job['job_id'], state='queued', pending=True, running=True, error='', result={})
        self.assertEqual(original_id, self.summary()['paperpiggy']['issue']['id'])

    def test_current_same_second_outcome_is_authoritative_and_deduplicated(self):
        history = [self.event('started', trigger='manual'), self.event('failed', error='old'),
                   self.event('success', result={'path': 'new.zip'})]
        current, job = self.fixture(history=history, state='success', operation='backup', running=False,
                                    result={'path': 'new.zip'}, finished_at='2026-10-30T08:00:00+08:00')
        self.assertIsNone(self.summary()['paperpiggy']['issue'])
        current.set_status(job['job_id'], state='failed', error='new failure', result={},
                           finished_at='2026-10-30T08:00:00+08:00')
        self.assertEqual('new failure', self.summary()['paperpiggy']['issue']['message'])

    def test_corrupt_status_returns_unknown_without_overwriting(self):
        current, job = self.fixture()
        path = current.base / 'status' / (job['job_id'] + '.json')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'{bad json')
        before = self.snapshot()
        result = self.client.get('/backup2/summary').json()
        self.assertFalse(result['ok'])
        self.assertIn('error', result)
        self.assertEqual(before, self.snapshot())

    def test_valid_json_with_corrupt_critical_fields_is_unknown_and_not_overwritten(self):
        from backup_jobs import atomic_json
        current, job = self.fixture()
        path = current.base / 'status' / (job['job_id'] + '.json')
        cases = [{'state': 'not-a-valid-state', 'running': 'false'},
                 {'state': 'running', 'pending': 'false'}, {'queued': 1},
                 {'operation': 'not-an-operation'}, {'done': -1}, {'total': True},
                 {'stage': ['wrong']}, {'result': {'path': {'invalid': 'object'}}},
                 {'result': {'warnings': ['good', {'bad': 'object'}]}},
                 {'finished_at': 'not-a-time'}]
        for value in cases:
            with self.subTest(value=value):
                atomic_json(path, value)
                before = self.snapshot()
                result = self.client.get('/backup2/summary').json()
                self.assertFalse(result['ok'], result)
                self.assertEqual(before, self.snapshot())
        atomic_json(path, {})
        history = current.base / 'history' / (job['job_id'] + '.json')
        atomic_json(history, [self.event('failed', error=['invalid error'])])
        self.assertFalse(self.client.get('/backup2/summary').json()['ok'])

    def test_started_trigger_overrides_inherited_raw_restore_operation(self):
        history = [self.event('started', trigger='timer'), self.event('failed', error='maintenance lock busy')]
        current, job = self.fixture(history=history, running=False, state='failed', operation='restore',
                                    error='maintenance lock busy', finished_at='2026-10-30T08:00:00+08:00')
        self.assertEqual('backup', self.summary()['paperpiggy']['issue']['operation'])
        current.set_status(job['job_id'], state='running', running=True, error='', operation='backup')
        current._event(job['job_id'], self.event('started', trigger='manual'))
        current._event(job['job_id'], self.event('success', result={'path': 'new.zip'}))
        current.set_status(job['job_id'], state='success', running=False, result={'path': 'new.zip'},
                           finished_at='2026-10-30T08:00:00+08:00')
        self.assertIsNone(self.summary()['paperpiggy']['issue'])

    def test_summary_remains_readable_during_paperpiggy_restore_gate(self):
        self.fixture(operation='restore', running=True, state='running')
        with mock.patch.object(api.RUN, 'active_paperpiggy_restore', return_value=True):
            self.assertTrue(self.summary()['paperpiggy']['running'])


if __name__ == '__main__':
    unittest.main()
