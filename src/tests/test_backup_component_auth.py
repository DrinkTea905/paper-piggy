from copy import deepcopy
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_component_auth as A
import backup_component_trust as T
import config as C


def manifest():
    return dict(schema=1, product=A.PRODUCT, component_version=2, app_version=C.APP_VERSION,
                helper_sha256="a" * 64, helper_size=1073741824, build_id="b" * 64,
                source_hashes={"backup_service.py": "c" * 64},
                runtime_hashes={"pythonw.exe": "d" * 64, "Lib/encodings/__init__.py": "e" * 64})


class ManifestTests(unittest.TestCase):
    def test_release_component_versions_increase_across_segment_boundaries(self):
        versions = ["0.0.0", "0.0.1", "0.0.999", "0.1.0", "0.999.999", "1.0.0", "999.999.999"]
        encoded = [A.release_component_version(value) for value in versions]
        self.assertEqual(encoded, sorted(set(encoded)))
        self.assertEqual(A.release_component_version("1.2.3"), 1002003000)
        self.assertTrue(all(value % 1000 == 0 for value in encoded))

    def test_release_component_version_rejects_out_of_bounds_and_aliases(self):
        for value in ("1.1000.0", "1000.0.0", "0.0.1000", "01.2.3", "1.2", "1.2.3\n", True, None):
            with self.subTest(value=value), self.assertRaises(A.ComponentAuthError):
                A.release_component_version(value)

    def test_round_trip_and_independent_result(self):
        original = manifest()
        checked = A.validate_manifest(original)
        original["source_hashes"].clear()
        self.assertEqual(len(checked["source_hashes"]), 1)
        self.assertEqual(A.strict_json(A.canonical_json(checked)), checked)

    def test_duplicate_json_at_each_level_rejected(self):
        for raw in ('{"a":1,"a":2}', '{"manifest":{"x":1,"x":1}}',
                    '{"x":1,"\\u0078":2}'):
            with self.subTest(raw=raw), self.assertRaises(A.ComponentAuthError):
                A.strict_json(raw)

    def test_invalid_json_and_nonintegral_values(self):
        for raw in (b"\xff", "{} trailing", "{", "NaN", "Infinity", "1.0", "1e2", "1" * 5000):
            with self.subTest(raw=raw[:30]), self.assertRaises(A.ComponentAuthError):
                A.strict_json(raw)

    def test_raw_input_bound(self):
        with patch.object(A, "MAX_ENVELOPE", 32), self.assertRaises(A.ComponentAuthError):
            A.strict_json(" " * 33)

    def test_unknown_or_missing_manifest_fields(self):
        for change in (lambda m: m.update(extra=True), lambda m: m.pop("helper_size")):
            value = manifest()
            change(value)
            with self.assertRaises(A.ComponentAuthError):
                A.validate_manifest(value)

    def test_schema_product_version_and_size_bounds(self):
        cases = [("schema", True), ("schema", 2), ("product", "OtherProduct"),
                 ("component_version", True), ("component_version", 0),
                 ("component_version", A.MAX_VERSION + 1), ("helper_size", 0),
                 ("helper_size", True), ("helper_size", A.MAX_HELPER_SIZE + 1),
                 ("helper_size", "500"), ("app_version", "0" + C.APP_VERSION),
                 ("app_version", C.APP_VERSION + "\n"), ("app_version", [1, 1, 18]),
                 ("helper_sha256", "A" * 64), ("build_id", "b" * 32)]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(A.ComponentAuthError):
                A.validate_manifest({**manifest(), field: value})

    def test_rollback_is_rejected_equal_version_allowed(self):
        self.assertEqual(A.validate_manifest(manifest(), minimum_version=2)["component_version"], 2)
        with self.assertRaises(A.ComponentAuthError):
            A.validate_manifest(manifest(), minimum_version=3)
        with self.assertRaises(A.ComponentAuthError):
            A.validate_manifest(manifest(), minimum_version=True)

    def test_file_paths_and_hashes_strict(self):
        paths = ["../x", "/x", "C:/x", "a\\b", "a//b", "a/./b", "NUL.py", "a/COM1",
                 "a/aux.txt", "a/LPT¹.txt", "file:stream", "file.", "file ", "x\x00y", "x\ud800"]
        for path in paths:
            with self.subTest(path=repr(path)), self.assertRaises(A.ComponentAuthError):
                A.validate_manifest({**manifest(), "source_hashes": {path: "a" * 64}})
        for value in ({}, [], {"x": 1}, {"A.py": "a" * 64, "a.py": "b" * 64}):
            with self.subTest(value=value), self.assertRaises(A.ComponentAuthError):
                A.validate_manifest({**manifest(), "runtime_hashes": value})


@unittest.skipUnless(sys.platform == "win32", "需要 Windows CNG/DPAPI")
class CngTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private, cls.public = A.generate_keypair()
        cls.other_private, cls.other_public = A.generate_keypair()

    @classmethod
    def tearDownClass(cls):
        cls.private[:] = b"\x00" * len(cls.private)
        cls.other_private[:] = b"\x00" * len(cls.other_private)

    def signed(self):
        return A.sign_manifest(manifest(), self.private)

    def test_real_cng_sign_verify_pss_randomized(self):
        first, second = self.signed(), self.signed()
        self.assertNotEqual(first, second)
        self.assertEqual(A.verify_envelope(first, self.public, minimum_version=2), manifest())
        self.assertEqual(A.public_from_private(self.private), self.public)
        self.assertEqual(len(base64.b64decode(A.strict_json(first)["signature"])), 384)

    def test_signed_component_can_add_multipart_module_without_new_trust_root(self):
        value = manifest()
        value["source_hashes"]["backup_parts.py"] = "f" * 64
        signed = A.sign_manifest(value, self.private)
        self.assertEqual(A.verify_envelope(signed, self.public), value)
        changed = A.strict_json(signed)
        changed["manifest"]["source_hashes"].pop("backup_parts.py")
        with self.assertRaises(A.ComponentAuthError):
            A.verify_envelope(A.canonical_json(changed), self.public)

    def test_fixed_pin_default_no_request_public_key(self):
        with patch.object(T, "PUBLIC_KEY_B64", base64.b64encode(self.public).decode()), \
                patch.object(T, "KEY_ID", A.key_id(self.public)):
            self.assertEqual(A.verify_envelope(self.signed()), manifest())
            envelope = A.strict_json(self.signed())
            envelope["public_key"] = base64.b64encode(self.other_public).decode()
            with self.assertRaises(A.ComponentAuthError):
                A.verify_envelope(A.canonical_json(envelope))

    def test_unconfigured_or_mismatched_pin_fails_closed(self):
        for pin, identifier in [("", ""), (base64.b64encode(self.public).decode(), "0" * 64)]:
            with patch.object(T, "PUBLIC_KEY_B64", pin), patch.object(T, "KEY_ID", identifier), \
                    self.assertRaises(A.ComponentAuthError):
                A.verify_envelope(self.signed())

    def test_wrong_key_and_unknown_key_id(self):
        with self.assertRaises(A.ComponentAuthError):
            A.verify_envelope(self.signed(), self.other_public)
        envelope = A.strict_json(self.signed())
        envelope["key_id"] = A.key_id(self.other_public)
        with self.assertRaises(A.ComponentAuthError):
            A.verify_envelope(A.canonical_json(envelope), self.other_public)

    def test_every_signed_manifest_field_is_bound(self):
        for field, value in [("component_version", 3), ("app_version", C.APP_VERSION.split(".")[0] + ".999.999"),
                             ("helper_size", 123), ("helper_sha256", "f" * 64),
                             ("build_id", "f" * 64), ("source_hashes", {"x.py": "f" * 64}),
                             ("runtime_hashes", {"pythonw.exe": "f" * 64})]:
            envelope = A.strict_json(self.signed())
            envelope["manifest"][field] = value
            with self.subTest(field=field), self.assertRaises(A.ComponentAuthError):
                A.verify_envelope(A.canonical_json(envelope), self.public)

    def test_bad_signature_and_padding(self):
        for signature in ("!", base64.b64encode(b"x" * 383).decode(),
                          base64.b64encode(b"x" * 384).decode()):
            envelope = A.strict_json(self.signed())
            envelope["signature"] = signature
            with self.subTest(length=len(signature)), self.assertRaises(A.ComponentAuthError):
                A.verify_envelope(A.canonical_json(envelope), self.public)
        envelope = A.strict_json(self.signed())
        envelope["algorithm"] = "RSA-PKCS1-SHA256"
        with self.assertRaises(A.ComponentAuthError):
            A.verify_envelope(A.canonical_json(envelope), self.public)

    def test_duplicate_signed_envelope_cannot_be_hidden_by_valid_signature(self):
        raw = self.signed().decode()
        raw = raw.replace('"helper_size":1073741824', '"helper_size":1073741824,"helper_size":1073741824')
        with self.assertRaises(A.ComponentAuthError):
            A.verify_envelope(raw, self.public)

    def test_public_blob_bounds(self):
        for public in (b"", self.public[:-1], self.public + b"x", self.public[:4] + b"\x00" * 4 + self.public[8:]):
            with self.subTest(length=len(public)), self.assertRaises(A.ComponentAuthError):
                A.verify_envelope(self.signed(), public)

    def test_signer_cannot_downgrade_rsa_key(self):
        with self.assertRaises(A.ComponentAuthError):
            A.sign_manifest(manifest(), b"invalid private blob")

    def test_dpapi_roundtrip_and_tamper(self):
        secret = bytearray(b"fixture secret not production")
        encrypted = A._dpapi(secret)
        self.assertNotIn(secret, encrypted)
        decrypted = A._dpapi(encrypted, decrypt=True)
        self.assertEqual(secret, decrypted)
        encrypted[-1] ^= 1
        with self.assertRaises(A.ComponentAuthError):
            A._dpapi(encrypted, decrypt=True)
        decrypted[:] = b"\x00" * len(decrypted)

    def test_release_key_file_is_encrypted_exclusive_and_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PaperPiggyRelease/component-key.dpapi"
            public = A.initialize_key(path)
            original = path.read_bytes()
            self.assertTrue(original.startswith(A._KEY_MAGIC))
            key = base64.b64decode(public["public_key_b64"])
            self.assertEqual(A.verify_envelope(A.sign_with_key_file(manifest(), path, public_key=key), key), manifest())
            with self.assertRaises(A.ComponentAuthError):
                A.sign_with_key_file(manifest(), path, public_key=self.other_public)
            with self.assertRaises(A.ComponentAuthError):
                A.initialize_key(path)
            self.assertEqual(original, path.read_bytes())
        with self.assertRaises(A.ComponentAuthError):
            A.initialize_key(Path(__file__).resolve().parents[2] / "build/unsafe-key.dpapi")
        with self.assertRaises(A.ComponentAuthError):
            A.initialize_key(Path(__file__).resolve().parents[2] / "build/../unsafe-key.dpapi")

    def test_key_file_hardlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.dpapi"
            path.write_bytes(b"fixture")
            alias = path.with_name("alias.dpapi")
            alias.hardlink_to(path)
            with self.assertRaises(A.ComponentAuthError):
                A.sign_with_key_file(manifest(), alias)


class SignerCliTests(unittest.TestCase):
    def test_init_only_explicit_and_outputs_public_data(self):
        spec = importlib.util.spec_from_file_location("component_signer", Path(__file__).resolve().parents[2]
                                                    / "installer/sign_backup_component.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        public = {"schema": 1, "key_id": "a" * 64, "public_key_b64": "public-only"}
        with tempfile.TemporaryDirectory() as tmp, patch.object(A, "initialize_key", return_value=public) as initialize:
            output = Path(tmp) / "public.json"
            self.assertEqual(cli.main(["--init-key", "--public-output", str(output)]), 0)
            initialize.assert_called_once_with(None)
            self.assertEqual(json.loads(output.read_text()), public)


if __name__ == "__main__":
    unittest.main()
