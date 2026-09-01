from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GREEN = load_module("cnc_green_contract_test", ROOT / "scripts" / "analyze_cnc_six_green.py")
BLINK = load_module(
    "cnc_blink_contract_test", ROOT / "scripts" / "analyze_cnc_six_green_blink.py"
)


def episode(episode_id: str, start: str) -> dict[str, str]:
    return {
        "episode_id": episode_id,
        "gate": "一楼电脑锣六台机",
        "recorder": "nvr-main-02",
        "channel": "49",
        "start_local": start,
        "end_local": start,
        "trigger_count": "1",
        "span_seconds": "60",
    }


def result(row: dict[str, str], statuses: dict[str, str], *, size: str = "1280x720") -> dict:
    start = BLINK.datetime.fromisoformat(row["start_local"])
    shift_date, shift = BLINK.shift_fields(start)
    records = []
    for machine in GREEN.MACHINE_ROIS:
        status = statuses[machine]
        records.append(
            {
                "episode_id": row["episode_id"],
                "start_local": row["start_local"],
                "shift_date": shift_date,
                "shift": shift,
                "working": int(GREEN.is_working_time(start)),
                "machine": machine,
                "frames_sampled": 20,
                "window_span_seconds": 9.5,
                "green_frames": 3 if status == "running" else 0,
                "green_offsets": "1.0;4.0;7.0" if status == "running" else "",
                "max_green_pixels": 20 if status == "running" else 0,
                "max_dominance_p995": 20.0 if status == "running" else 0,
                "max_green": 255 if status == "running" else 70,
                "status": status,
                "ambiguous_review_extended": 0,
                "decoded_sizes": size,
                "error": "",
            }
        )
    return {
        "episode_id": row["episode_id"],
        "start_local": row["start_local"],
        "frames_sampled": 20,
        "window_span_seconds": 9.5,
        "records": records,
        "error": "",
    }


class CncFloor1RuntimeContractTests(unittest.TestCase):
    def test_fixed_channel_and_current_calibration(self) -> None:
        self.assertEqual(GREEN.CONFIG["policy_version"], "cnc-floor1-green-blink-v2.1")
        self.assertEqual(GREEN.CONFIG["camera"]["channel"], 49)
        self.assertEqual(GREEN.CONFIG["camera"]["track"], 4901)
        self.assertEqual(GREEN.REFERENCE_SIZE, (1280, 720))
        self.assertEqual(set(GREEN.MACHINE_ROIS), {"1", "2", "3", "4", "5", "6"})
        self.assertEqual(
            GREEN.CONFIG["sampling"]["ambiguous_review_window_seconds"], 20.0
        )
        self.assertEqual(
            GREEN.CONFIG["sampling"]["strong_single_frame_green_pixels"], 12
        )

    def test_quality_pass_writes_comparable_metrics(self) -> None:
        rows = [
            episode("cnc-001", "2026-08-28T08:00:00"),
            episode("cnc-002", "2026-08-28T08:05:00"),
        ]
        statuses = {machine: "stopped" for machine in GREEN.MACHINE_ROIS}
        statuses["1"] = "running"
        results = [result(row, statuses) for row in rows]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            quality = BLINK.write_outputs(output, rows, results, {"test": True})
            self.assertEqual(quality, "pass")
            qc = json.loads((output / "qc-summary.json").read_text(encoding="utf-8"))
            metrics = json.loads(
                (output / "metrics-input.json").read_text(encoding="utf-8")
            )
            self.assertEqual(qc["quality_gate"], "pass")
            self.assertEqual(metrics["quality_gate"], "pass")
            self.assertTrue(metrics["metrics"])
            with (output / "machine-rates.csv").open(encoding="utf-8") as handle:
                summary = list(csv.DictReader(handle))
            machine_one = next(
                row
                for row in summary
                if row["period"] == "有效生产时段" and row["machine"] == "1"
            )
            self.assertEqual(machine_one["running_rate"], "100.00%")

    def test_unknown_coverage_fails_closed(self) -> None:
        rows = [episode("cnc-001", "2026-08-28T08:00:00")]
        statuses = {machine: "running" for machine in GREEN.MACHINE_ROIS}
        broken = result(rows[0], statuses)
        broken["records"][0]["status"] = "unknown"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            quality = BLINK.write_outputs(output, rows, [broken], {"test": True})
            self.assertEqual(quality, "needs_review")
            qc = json.loads((output / "qc-summary.json").read_text(encoding="utf-8"))
            self.assertTrue(any("覆盖率" in reason for reason in qc["reasons"]))

    def test_wrong_frame_size_fails_closed(self) -> None:
        rows = [episode("cnc-001", "2026-08-28T08:00:00")]
        statuses = {machine: "running" for machine in GREEN.MACHINE_ROIS}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            quality = BLINK.write_outputs(
                output, rows, [result(rows[0], statuses, size="2560x1440")], {"test": True}
            )
            self.assertEqual(quality, "needs_review")
            qc = json.loads((output / "qc-summary.json").read_text(encoding="utf-8"))
            self.assertTrue(any("尺寸" in reason for reason in qc["reasons"]))

    def test_long_all_zero_result_is_not_treated_as_official(self) -> None:
        start = datetime.fromisoformat("2026-08-28T08:00:00")
        rows = [
            episode(f"cnc-{index + 1:03d}", (start + timedelta(minutes=5 * index)).isoformat())
            for index in range(12)
        ]
        statuses = {machine: "stopped" for machine in GREEN.MACHINE_ROIS}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            quality = BLINK.write_outputs(
                output, rows, [result(row, statuses) for row in rows], {"test": True}
            )
            self.assertEqual(quality, "needs_review")
            qc = json.loads((output / "qc-summary.json").read_text(encoding="utf-8"))
            self.assertTrue(any("不能按0%" in reason for reason in qc["reasons"]))


if __name__ == "__main__":
    unittest.main()
