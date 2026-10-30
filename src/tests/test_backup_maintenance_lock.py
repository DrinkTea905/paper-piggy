from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from backup_jobs import BackupBusy, paperpiggy_maintenance_lock


class BackupMaintenanceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        patch = mock.patch.object(server.C, 'DATA', self.data)
        patch.start()
        self.addCleanup(patch.stop)
        state = mock.patch.object(server, 'BUILD', server._BuildState(running=False, stage=''))
        state.start()
        self.addCleanup(state.stop)
        self.addCleanup(lambda: server.BUILD.__setitem__('running', False))

    def test_build_state_and_background_backup_share_the_same_lock(self):
        server.BUILD['running'] = True
        with self.assertRaises(BackupBusy):
            with paperpiggy_maintenance_lock(self.data):
                pass
        server.BUILD['running'] = False
        with paperpiggy_maintenance_lock(self.data):
            pass

    def test_dictionary_update_obeys_the_lock(self):
        with paperpiggy_maintenance_lock(self.data):
            with self.assertRaises(BackupBusy):
                server.BUILD.update(running=True, stage='semantic')
            self.assertFalse(server.BUILD['running'])
        server.BUILD.update(running=True)
        server.BUILD.update(running=False)
        with paperpiggy_maintenance_lock(self.data):
            pass

    def test_build_request_returns_busy_without_starting_a_child(self):
        with paperpiggy_maintenance_lock(self.data), \
                mock.patch.object(server, '_spawn_build_process') as spawn:
            result = server._run_build('light', detailed=True)
        self.assertFalse(result['ok'])
        self.assertTrue(result['busy'])
        self.assertFalse(server.BUILD['running'])
        spawn.assert_not_called()

    def test_topics_lock_race_does_not_leave_a_false_running_flag(self):
        with paperpiggy_maintenance_lock(self.data), \
                mock.patch.dict(server.R.STATE, mode='full'), \
                mock.patch.dict(server.TOPICS_BUILD, running=False, msg=''), \
                mock.patch.object(server, '_maintenance_busy_locked', return_value=None):
            with self.assertRaises(BackupBusy):
                server.topics_rebuild()
            self.assertFalse(server.TOPICS_BUILD['running'])
            self.assertFalse(server.BUILD['running'])


    def test_foreign_running_status_does_not_block_local_maintenance(self):
        from backup_jobs import JobStore
        foreign = JobStore(self.data / 'backup_jobs', machine_id='previous-machine')
        old = next(job for job in foreign.ensure_defaults() if job['kind'] == 'paperpiggy')
        foreign.set_status(old['job_id'], running=True, operation='backup', state='running')
        status_path = foreign.base / 'status' / (old['job_id'] + '.json')
        before = status_path.read_bytes()
        with mock.patch.object(server, 'BACKUP', {'running': False}):
            self.assertFalse(server._backup_running())
            local = JobStore(self.data / 'backup_jobs')
            jobs = local.ensure_defaults()
            job = next(row for row in jobs if row['kind'] == 'paperpiggy'
                       and row['machine_id'] == local.machine_id)
            local.set_status(job['job_id'], running=True, operation='backup', state='running')
            self.assertTrue(server._backup_running())
        self.assertEqual(before, status_path.read_bytes())


if __name__ == '__main__':
    unittest.main()
