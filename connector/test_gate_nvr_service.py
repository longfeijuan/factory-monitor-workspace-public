import importlib.util
import sys
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("gate_nvr_service.py")
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", MODULE_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


class CoverageTests(unittest.TestCase):
    def test_merges_segments_and_reports_gap(self):
        start = datetime.fromisoformat("2026-08-01T00:00:00+08:00")
        end = datetime.fromisoformat("2026-08-01T01:00:00+08:00")
        status, gaps, evidence_ids = gate._merge_coverage(
            [
                {"start": "2026-07-31T16:00:00Z", "end": "2026-07-31T16:20:00Z", "evidenceId": "ev-a"},
                {"start": "2026-07-31T16:30:00Z", "end": "2026-07-31T17:00:00Z", "evidenceId": "ev-b"},
            ],
            start,
            end,
        )
        self.assertEqual(status, "partial")
        self.assertEqual(len(gaps), 1)
        self.assertEqual(evidence_ids, ["ev-a", "ev-b"])

    def test_no_segments_is_missing_not_available(self):
        start = datetime.fromisoformat("2026-08-01T00:00:00+08:00")
        end = datetime.fromisoformat("2026-08-01T01:00:00+08:00")
        status, gaps, evidence_ids = gate._merge_coverage([], start, end)
        self.assertEqual(status, "missing")
        self.assertEqual(len(gaps), 1)
        self.assertEqual(evidence_ids, [])


class CredentialPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.credentials = {
            recorder: gate.Credential("192.0.2.1", "readonly", "not-a-real-password")
            for recorder in gate.RECORDER_ORDER
        }

    @mock.patch.object(gate, "import_credentials_from_dingtalk")
    @mock.patch.object(gate, "load_keychain_credentials", return_value=None)
    @mock.patch.object(gate, "keychain_available", return_value=False)
    def test_non_macos_import_stays_in_memory(self, _available, _load, imported):
        imported.return_value = self.credentials
        credentials, source = gate.load_credentials(True, "dws")
        self.assertEqual(credentials, self.credentials)
        self.assertEqual(source, "dingtalk-memory")

    @mock.patch.object(gate, "save_keychain_credentials")
    @mock.patch.object(gate, "import_credentials_from_dingtalk")
    @mock.patch.object(gate, "load_keychain_credentials", return_value=None)
    @mock.patch.object(gate, "keychain_available", return_value=True)
    def test_macos_import_is_saved_to_keychain(self, _available, _load, imported, saved):
        imported.return_value = self.credentials
        credentials, source = gate.load_credentials(True, "dws")
        self.assertEqual(credentials, self.credentials)
        self.assertEqual(source, "dingtalk-to-keychain")
        saved.assert_called_once_with(self.credentials)

    @mock.patch.object(gate, "load_keychain_credentials", return_value=None)
    def test_missing_credentials_gives_actionable_error(self, _load):
        with self.assertRaisesRegex(gate.ConnectorError, "--import-from-dingtalk"):
            gate.load_credentials(False, "dws")


if __name__ == "__main__":
    unittest.main()
