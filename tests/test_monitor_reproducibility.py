from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CONTEXT = load_module("monitor_query_context", ROOT / "scripts/monitor_query_context.py")
CONTRACT = load_module("monitor_result_contract", ROOT / "scripts/monitor_result_contract.py")
COMPARE = load_module("compare_monitor_results", ROOT / "scripts/compare_monitor_results.py")


class MonitorReproducibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def context(self, parameters: dict) -> dict:
        return CONTEXT.build_context(
            root=ROOT,
            task_type="fixture-runtime",
            task_policy_version="fixture-v1",
            start=datetime(2026, 1, 1, 8),
            end=datetime(2026, 1, 1, 20),
            parameters=parameters,
            task_config_path=None,
            strict=False,
        )

    def seal(self, context: dict, metrics: dict, name: str) -> Path:
        context_path = self.work / f"{name}-context.json"
        metrics_path = self.work / f"{name}-metrics.json"
        output_path = self.work / f"{name}-result.json"
        context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
        CONTRACT.finalize(context_path, metrics_path, output_path)
        return output_path

    def test_query_identity_is_order_independent_but_input_sensitive(self):
        left = self.context({"shift": "day", "channels": [59, 31]})
        reordered = self.context({"channels": [59, 31], "shift": "day"})
        changed = self.context({"shift": "night", "channels": [59, 31]})
        self.assertEqual(left["query_id"], reordered["query_id"])
        self.assertNotEqual(left["query_id"], changed["query_id"])
        self.assertEqual(left["git_commit"], reordered["git_commit"])

    def test_rate_difference_within_one_point_is_accepted(self):
        context = self.context({"shift": "day"})
        left = self.seal(
            context,
            {
                "quality_gate": "pass",
                "metrics": {"overall.rate": {"value": 54.7, "unit": "percentage_point"}},
            },
            "left",
        )
        right = self.seal(
            context,
            {
                "quality_gate": "pass",
                "metrics": {"overall.rate": {"value": 55.4, "unit": "percentage_point"}},
            },
            "right",
        )
        result = COMPARE.compare(left, right)
        self.assertTrue(result["accepted_for_user_goal"])
        self.assertFalse(result["identical"])

    def test_rate_difference_above_one_point_is_rejected(self):
        context = self.context({"shift": "day"})
        left = self.seal(
            context,
            {
                "quality_gate": "pass",
                "metrics": {"overall.rate": {"value": 52.0, "unit": "percentage_point"}},
            },
            "left",
        )
        right = self.seal(
            context,
            {
                "quality_gate": "pass",
                "metrics": {"overall.rate": {"value": 54.0, "unit": "percentage_point"}},
            },
            "right",
        )
        result = COMPARE.compare(left, right)
        self.assertFalse(result["accepted_for_user_goal"])
        self.assertFalse(result["all_metrics_within_tolerance"])

    def test_needs_review_result_is_never_accepted(self):
        context = self.context({"shift": "day"})
        metrics = {
            "quality_gate": "needs_review",
            "metrics": {"overall.rate": {"value": 54.0, "unit": "percentage_point"}},
        }
        left = self.seal(context, metrics, "left")
        right = self.seal(context, metrics, "right")
        result = COMPARE.compare(left, right)
        self.assertFalse(result["accepted_for_user_goal"])
        self.assertTrue(result["identical"])

    def test_simple_steel_analyzer_emits_project_metric_input(self):
        image_path = self.work / "frame.jpg"
        Image.new("RGB", (2560, 1440), "black").save(image_path, quality=80)
        manifests = []
        for channel in (59, 31):
            manifest = self.work / f"ch{channel}.csv"
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=("start_local", "image"))
                writer.writeheader()
                writer.writerow({"start_local": "2026-01-01T08:00:00", "image": str(image_path)})
            manifests.append(manifest)
        output = self.work / "simple-steel"
        completed = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / ".agents/skills/floor1-simple-steel-monitor-audit/scripts/analyze_shift.py"
                ),
                "--ch59-manifest",
                str(manifests[0]),
                "--ch31-manifest",
                str(manifests[1]),
                "--output",
                str(output),
                "--shift",
                "day",
                "--start",
                "2026-01-01T08:00:00",
                "--end",
                "2026-01-01T08:05:00",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        metrics = json.loads((output / "metrics-input.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["quality_gate"], "pass")
        self.assertEqual(len(metrics["metrics"]), 8)
        self.assertEqual(metrics["metrics"]["effective.overall.runtime_rate"]["value"], 0.0)


if __name__ == "__main__":
    unittest.main()
