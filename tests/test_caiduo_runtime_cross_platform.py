from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CAIDUO = load_module(
    "caiduo_runtime_cross_platform_test",
    ROOT
    / ".agents/skills/caiduo-high-speed-saw-runtime/scripts/analyze_runtime.py",
)


class CaiduoRuntimeCrossPlatformTest(unittest.TestCase):
    def test_project_connector_is_found_from_repo_skill(self):
        expected = (ROOT / "connector/gate_nvr_service.py").resolve()
        with mock.patch.object(CAIDUO.Path, "cwd", return_value=ROOT.parent):
            self.assertEqual(CAIDUO.resolve_connector_path(), expected)

    def test_windows_style_dingtalk_import_stays_in_process(self):
        calls = []

        def load_credentials(import_from_dingtalk: bool, dws: str):
            calls.append((import_from_dingtalk, dws))
            return {
                "nvr-caiduo": SimpleNamespace(
                    host="192.0.2.10", username="readonly", password="fixture-secret"
                )
            }, "dingtalk-memory"

        connector = SimpleNamespace(
            default_dws=lambda: "dws.exe", load_credentials=load_credentials
        )
        with mock.patch.object(CAIDUO, "_load_connector_module", return_value=connector):
            credential, source = CAIDUO.load_credential(
                "nvr-caiduo",
                import_from_dingtalk=True,
                dws=None,
                connector_path=ROOT / "connector/gate_nvr_service.py",
            )

        self.assertEqual(calls, [(True, "dws.exe")])
        self.assertEqual(source, "dingtalk-memory")
        self.assertEqual(credential.host, "192.0.2.10")
        self.assertEqual(credential.username, "readonly")
        self.assertEqual(credential.password, "fixture-secret")

    def test_credential_failure_is_sanitized(self):
        connector = SimpleNamespace(
            default_dws=lambda: "dws.exe",
            load_credentials=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("sensitive upstream text")
            ),
        )
        with mock.patch.object(CAIDUO, "_load_connector_module", return_value=connector):
            with self.assertRaisesRegex(SystemExit, "^nvr_credentials_unavailable$"):
                CAIDUO.load_credential(
                    "nvr-caiduo",
                    import_from_dingtalk=True,
                    dws=None,
                    connector_path=ROOT / "connector/gate_nvr_service.py",
                )


if __name__ == "__main__":
    unittest.main()
