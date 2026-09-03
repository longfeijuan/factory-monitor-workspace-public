import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "monitor_self_check.py"
SPEC = importlib.util.spec_from_file_location("monitor_self_check", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ToolRequirementProfileTests(unittest.TestCase):
    def test_full_profile_keeps_previous_node_requirements(self):
        for name in ("python", "node", "pnpm"):
            with self.subTest(name=name):
                self.assertTrue(
                    MODULE.tool_is_required(name, lightweight=False, source_sync=False)
                )

    def test_lightweight_profile_only_requires_python_for_normal_queries(self):
        self.assertTrue(
            MODULE.tool_is_required("python", lightweight=True, source_sync=False)
        )
        for name in ("node", "pnpm", "dws"):
            with self.subTest(name=name):
                self.assertFalse(
                    MODULE.tool_is_required(name, lightweight=True, source_sync=False)
                )

    def test_source_sync_still_requires_dingtalk_cli(self):
        self.assertTrue(
            MODULE.tool_is_required("dws", lightweight=True, source_sync=True)
        )


if __name__ == "__main__":
    unittest.main()
