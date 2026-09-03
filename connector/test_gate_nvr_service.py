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
        builtin_hosts = gate.load_builtin_hosts()
        self.credentials = {
            recorder: gate.Credential(builtin_hosts[recorder], "readonly", "not-a-real-password")
            for recorder in gate.RECORDER_ORDER
        }

    def test_builtin_endpoint_catalog_is_complete_and_grouped(self):
        hosts = gate.load_builtin_hosts()
        self.assertEqual(tuple(hosts), gate.RECORDER_ORDER)
        self.assertEqual(len(set(hosts.values())), 4)

    def test_macos_keychain_is_isolated_from_safety_automation(self):
        self.assertEqual(
            gate._keychain_service("nvr-main-02"),
            "com.codex.factory-monitor-workspace.nvr-main-02",
        )
        self.assertNotEqual(
            gate._keychain_service("nvr-main-02"),
            "com.codex.gate-person-audit.nvr-main-02",
        )

    @mock.patch.object(gate, "keychain_available", return_value=False)
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=False)
    @mock.patch.object(gate, "import_credentials_from_dingtalk")
    @mock.patch.object(gate, "load_stored_credentials", return_value=(None, None))
    def test_non_macos_import_stays_in_memory(
        self, _stored, imported, _windows_available, _keychain_available
    ):
        imported.return_value = self.credentials
        credentials, source = gate.load_credentials(True, "dws")
        self.assertEqual(credentials, self.credentials)
        self.assertEqual(source, "dingtalk-memory")

    @mock.patch.object(gate, "save_windows_credentials")
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=True)
    @mock.patch.object(gate, "import_credentials_from_dingtalk")
    @mock.patch.object(gate, "load_stored_credentials", return_value=(None, None))
    def test_windows_import_is_saved_to_credential_manager(
        self, _stored, imported, _windows_available, saved
    ):
        imported.return_value = self.credentials
        credentials, source = gate.load_credentials(True, "dws")
        self.assertEqual(credentials, self.credentials)
        self.assertEqual(source, "dingtalk-to-windows-credential-manager")
        saved.assert_called_once_with(self.credentials)

    @mock.patch.object(gate, "save_keychain_credentials")
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=False)
    @mock.patch.object(gate, "import_credentials_from_dingtalk")
    @mock.patch.object(gate, "load_stored_credentials", return_value=(None, None))
    @mock.patch.object(gate, "keychain_available", return_value=True)
    def test_macos_import_is_saved_to_keychain(
        self, _keychain_available, _stored, imported, _windows_available, saved
    ):
        imported.return_value = self.credentials
        credentials, source = gate.load_credentials(True, "dws")
        self.assertEqual(credentials, self.credentials)
        self.assertEqual(source, "dingtalk-to-keychain")
        saved.assert_called_once_with(self.credentials)

    @mock.patch.object(gate, "load_stored_credentials", return_value=(None, None))
    def test_missing_credentials_gives_actionable_error(self, _load):
        with self.assertRaisesRegex(gate.ConnectorError, "--setup-credentials"):
            gate.load_credentials(False, "dws")

    @mock.patch.object(gate, "load_stored_credentials")
    def test_stored_windows_credentials_do_not_require_dingtalk(self, stored):
        stored.return_value = (self.credentials, "windows-credential-manager")
        credentials, source = gate.load_credentials(False, "dws")
        self.assertEqual(credentials, self.credentials)
        self.assertEqual(source, "windows-credential-manager")

    @mock.patch.object(gate, "_read_windows_secret")
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=True)
    def test_load_windows_credentials_reads_all_four_without_exposing_secrets(
        self, _available, read_secret
    ):
        read_secret.return_value = (
            '{"username":"readonly","password":"fixture-secret"}'
        )
        credentials = gate.load_windows_credentials()
        self.assertEqual(set(credentials or {}), set(gate.RECORDER_ORDER))
        self.assertEqual(
            {recorder: credential.host for recorder, credential in (credentials or {}).items()},
            gate.load_builtin_hosts(),
        )
        self.assertEqual(read_secret.call_count, 4)

    @mock.patch.object(gate, "_write_windows_secret")
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=True)
    def test_save_windows_credentials_uses_separate_manager_entries(self, _available, write_secret):
        gate.save_windows_credentials(self.credentials)
        self.assertEqual(write_secret.call_count, 4)
        targets = [call.args[0] for call in write_secret.call_args_list]
        self.assertEqual(
            targets,
            [gate._windows_credential_target(recorder) for recorder in gate.RECORDER_ORDER],
        )
        self.assertTrue(all('"host"' not in call.args[1] for call in write_secret.call_args_list))

    @mock.patch.object(gate, "save_windows_credentials")
    @mock.patch.object(gate.getpass, "getpass", side_effect=["main-secret", "caiduo-secret"])
    @mock.patch(
        "builtins.input",
        side_effect=[
            "main-readonly",
            "caiduo-readonly",
        ],
    )
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=True)
    def test_interactive_setup_reuses_main_credentials_without_printing_passwords(
        self, _available, _input, _getpass, saved
    ):
        credentials = gate.setup_windows_credentials_interactive()
        self.assertEqual(
            {recorder: credential.host for recorder, credential in credentials.items()},
            gate.load_builtin_hosts(),
        )
        self.assertEqual(credentials["nvr-main-02"].username, "main-readonly")
        self.assertEqual(credentials["nvr-main-03"].password, "main-secret")
        self.assertEqual(credentials["nvr-caiduo"].username, "caiduo-readonly")
        saved.assert_called_once_with(credentials)

    @mock.patch.object(gate, "save_windows_credentials")
    @mock.patch.object(gate.getpass, "getpass", side_effect=["main-secret", ""])
    @mock.patch("builtins.input", side_effect=["main-readonly", ""])
    @mock.patch.object(gate, "windows_credential_manager_available", return_value=True)
    def test_interactive_setup_can_reuse_main_credentials_for_caiduo(
        self, _available, _input, _getpass, saved
    ):
        credentials = gate.setup_windows_credentials_interactive()
        self.assertEqual(credentials["nvr-caiduo"].username, "main-readonly")
        self.assertEqual(credentials["nvr-caiduo"].password, "main-secret")
        saved.assert_called_once_with(credentials)


if __name__ == "__main__":
    unittest.main()
