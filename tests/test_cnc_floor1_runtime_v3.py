from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "cnc-floor1-runtime-v3.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BLINK = load_module(
    "cnc_blink_v3_contract_test", ROOT / "scripts" / "analyze_cnc_six_green_blink.py"
)
BLINK.configure(CONFIG)


def episode(source_id: str, channel: int, track: int) -> dict[str, str]:
    return {
        "episode_id": f"{source_id}-cnc-001",
        "source_id": source_id,
        "source_label": source_id,
        "gate": "一楼电脑锣六台机",
        "recorder": "nvr-main-02",
        "channel": str(channel),
        "track": str(track),
        "start_local": "2026-09-01T08:00:00",
        "end_local": "2026-09-01T08:01:00",
        "trigger_count": "1",
        "span_seconds": "60",
    }


def source_result(row: dict[str, str], statuses: dict[str, str]) -> dict:
    start = BLINK.datetime.fromisoformat(row["start_local"])
    shift_date, shift = BLINK.shift_fields(start)
    records = []
    for machine, status in statuses.items():
        spec = BLINK.MACHINE_SPECS[machine]
        records.append(
            {
                "episode_id": row["episode_id"],
                "start_local": row["start_local"],
                "shift_date": shift_date,
                "shift": shift,
                "working": 1,
                "machine": machine,
                "source_id": row["source_id"],
                "source_label": spec["source_label"],
                "recorder": row["recorder"],
                "channel": int(row["channel"]),
                "track": int(row["track"]),
                "frames_sampled": 20,
                "window_span_seconds": 9.5,
                "green_frames": 3 if status == "running" else 0,
                "green_offsets": "1.0;4.0;7.0" if status == "running" else "",
                "max_green_pixels": 20 if status == "running" else 0,
                "max_dominance_p995": 20.0 if status == "running" else 0,
                "max_green": 255 if status == "running" else 70,
                "status": status,
                "ambiguous_review_extended": 0,
                "decoded_sizes": "x".join(str(value) for value in spec["reference_size"]),
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


class CncFloor1RuntimeV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        BLINK.configure(CONFIG)

    def test_physical_machine_source_mapping(self) -> None:
        self.assertEqual(
            BLINK.ACTIVE_CONFIG["policy_version"],
            "cnc-floor1-green-blink-v3.0-dual-view",
        )
        self.assertEqual(BLINK.MACHINE_SPECS["4"]["source_id"], "fisheye1")
        self.assertEqual(BLINK.MACHINE_SPECS["4"]["channel"], 2)
        self.assertEqual(BLINK.MACHINE_SPECS["4"]["roi"], (1980, 390, 2125, 585))
        self.assertEqual(BLINK.MACHINE_SPECS["5"]["source_id"], "passage49")
        self.assertEqual(BLINK.MACHINE_SPECS["5"]["channel"], 49)
        self.assertEqual(BLINK.MACHINE_SPECS["5"]["roi"], (1145, 195, 1220, 305))
        self.assertEqual(set(BLINK.MACHINE_SPECS), {"1", "2", "3", "4", "5", "6"})

    def test_dual_view_quality_pass(self) -> None:
        passage = episode("passage49", 49, 4901)
        fisheye = episode("fisheye1", 2, 201)
        results = [
            source_result(
                passage,
                {"1": "running", "2": "stopped", "3": "stopped", "5": "running", "6": "stopped"},
            ),
            source_result(fisheye, {"4": "running"}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            quality = BLINK.write_outputs(
                Path(temporary), [passage, fisheye], results, {"config": str(CONFIG)}
            )
            self.assertEqual(quality, "pass")

    def test_wrong_machine_source_fails_closed(self) -> None:
        passage = episode("passage49", 49, 4901)
        fisheye = episode("fisheye1", 2, 201)
        passage_result = source_result(
            passage,
            {"1": "running", "2": "stopped", "3": "stopped", "5": "running", "6": "stopped"},
        )
        passage_result["records"][3]["source_id"] = "fisheye1"
        results = [passage_result, source_result(fisheye, {"4": "running"})]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            quality = BLINK.write_outputs(
                output, [passage, fisheye], results, {"config": str(CONFIG)}
            )
            self.assertEqual(quality, "needs_review")
            qc = json.loads((output / "qc-summary.json").read_text(encoding="utf-8"))
            self.assertTrue(any("来源映射" in reason for reason in qc["reasons"]))


if __name__ == "__main__":
    unittest.main()
