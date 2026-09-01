from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOTS = load_module("nvr_h264_snapshots_test", ROOT / "scripts/nvr_h264_snapshots.py")


class NvrH264CredentialImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.episodes = self.work / "episodes.csv"
        self.episodes.write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, import_from_dingtalk: bool) -> None:
        output = self.work / ("import" if import_from_dingtalk else "default")
        argv = ["nvr_h264_snapshots.py", str(self.episodes), str(output)]
        if import_from_dingtalk:
            argv.append("--import-from-dingtalk")
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                SNAPSHOTS.MODULE,
                "load_credentials",
                return_value=({}, "fixture"),
            ) as loader,
        ):
            self.assertEqual(SNAPSHOTS.main(), 0)
        loader.assert_called_once_with(import_from_dingtalk, "dws")
        self.assertTrue((output / "summary.json").is_file())

    def test_dingtalk_import_is_disabled_by_default(self):
        self.run_main(False)

    def test_dingtalk_import_can_be_enabled_explicitly(self):
        self.run_main(True)


if __name__ == "__main__":
    unittest.main()
