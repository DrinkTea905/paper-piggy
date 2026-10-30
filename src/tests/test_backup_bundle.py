import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_bundle as bundle
import backup_service
import backup_component_auth as auth


@unittest.skipUnless(sys.platform == 'win32', 'Windows installer signature verification')
class BackupBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key, cls.public_key = auth.generate_keypair()

    @classmethod
    def tearDownClass(cls):
        cls.private_key[:] = b'\0' * len(cls.private_key)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'src'
        self.source.mkdir()
        self.component = self.root / 'build/backup-component'
        self.component.mkdir(parents=True)
        installer = self.root / 'installer'
        installer.mkdir()
        (installer / 'backup_component.iss').write_bytes(b'installer fixture')
        (installer / 'build_backup_component.py').write_bytes(b'builder fixture')
        hashes = {}
        for name in backup_service.MODULES:
            path = self.source / name
            path.write_bytes(name.encode())
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        exe = self.component / 'backup_component.exe'
        exe.write_bytes(b'isolated component fixture')
        self.meta = {'schema': 1, 'source_hashes': hashes,
                     'build_id': hashlib.sha256(b'build fixture').hexdigest(),
                     'runtime_hashes': {'pythonw.exe': hashlib.sha256(b'runtime fixture').hexdigest()},
                     'component_version': auth.release_component_version(bundle.C.APP_VERSION),
                     'app_version': bundle.C.APP_VERSION,
                     'installer_sha256': hashlib.sha256((installer / 'backup_component.iss').read_bytes()).hexdigest(),
                     'builder_sha256': hashlib.sha256((installer / 'build_backup_component.py').read_bytes()).hexdigest(),
                     'sha256': hashlib.sha256(exe.read_bytes()).hexdigest()}
        (self.component / 'backup_component.json').write_text(json.dumps(self.meta), encoding='utf-8')
        patch = mock.patch.object(bundle, 'SRC', self.source)
        patch.start()
        self.addCleanup(patch.stop)
        pin = mock.patch.object(auth, 'pinned_public_key', return_value=self.public_key)
        pin.start()
        self.addCleanup(pin.stop)
        self.write_signature()

    def write_signature(self, change=None):
        executable = self.component / 'backup_component.exe'
        manifest = bundle.backup_component_manifest(self.meta, executable)
        if change:
            change(manifest)
        (self.component / 'backup_component.sig.json').write_bytes(auth.sign_manifest(manifest, self.private_key))

    def test_source_manifest_includes_component_and_its_hash_metadata(self):
        files = bundle.source_app_files()
        self.assertEqual(self.component / 'backup_component.exe', files['backup_component.exe'])
        self.assertEqual(self.component / 'backup_component.json', files['backup_component.json'])
        self.assertEqual(self.component / 'backup_component.sig.json', files['backup_component.sig.json'])

    def test_missing_or_tampered_signature_blocks_packaging(self):
        signature = self.component / 'backup_component.sig.json'
        signature.write_bytes(b'{}')
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()
        signature.unlink()
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()

    def test_valid_signature_for_other_build_cannot_be_packaged(self):
        self.write_signature(lambda m: m.update(build_id=hashlib.sha256(b'other build').hexdigest()))
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()

    def test_private_acceptance_revision_cannot_be_published(self):
        self.meta['component_version'] += 1
        (self.component / 'backup_component.json').write_text(json.dumps(self.meta), encoding='utf-8')
        self.write_signature()
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()

    def test_source_change_requires_fresh_component(self):
        (self.source / 'backup_runner.py').write_bytes(b'updated code')
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()

    def test_modified_or_missing_executable_blocks_packaging(self):
        exe = self.component / 'backup_component.exe'
        exe.write_bytes(b'changed')
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()
        exe.unlink()
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()

    def test_installer_change_requires_rebuilt_component(self):
        (self.root / 'installer/backup_component.iss').write_bytes(b'updated installer')
        with self.assertRaises(SystemExit):
            bundle.backup_component_files()


if __name__ == '__main__':
    unittest.main()
