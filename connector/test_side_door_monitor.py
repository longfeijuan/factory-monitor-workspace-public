#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "side_door_monitor.py"
SPEC = importlib.util.spec_from_file_location("side_door_monitor", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CONFIG = {
    "door_face_roi": [0.20, 0.10, 0.80, 0.80],
    "center_seam_roi": [0.48, 0.15, 0.52, 0.72],
    "bottom_gap_roi": [0.22, 0.70, 0.78, 0.80],
    "thresholds": {
        "min_frame_mean": 8,
        "min_frame_stddev": 4,
        "open_baseline_diff": 0.26,
        "center_contrast": 30,
        "center_bright_fraction": 0.08,
        "bottom_contrast": 45,
        "bottom_bright_run": 0.22,
    },
}


def baseline_image() -> Image.Image:
    image = Image.new("RGB", (640, 480), (105, 105, 105))
    draw = ImageDraw.Draw(image)
    draw.rectangle((128, 48, 512, 384), fill=(125, 125, 125))
    draw.rectangle((312, 72, 328, 345), fill=(45, 45, 45))
    draw.rectangle((140, 336, 500, 384), fill=(55, 55, 55))
    draw.ellipse((60, 50, 100, 90), fill=(190, 190, 190))
    return image


class SideDoorAnalysisTests(unittest.TestCase):
    def test_closed_frame_stays_closed(self) -> None:
        baseline = baseline_image()
        result = MODULE.analyze_door(baseline.copy(), baseline, CONFIG)
        self.assertEqual(result.state, "closed")

    def test_bright_bottom_gap_is_ajar(self) -> None:
        baseline = baseline_image()
        frame = baseline.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((150, 344, 490, 359), fill=(255, 255, 255))
        result = MODULE.analyze_door(frame, baseline, CONFIG)
        self.assertEqual(result.state, "ajar")

    def test_structural_bottom_gap_can_be_ignored(self) -> None:
        baseline = baseline_image()
        frame = baseline.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((150, 344, 490, 359), fill=(255, 255, 255))
        config = {
            **CONFIG,
            "use_bottom_gap": False,
            "door_face_roi": [0.20, 0.10, 0.80, 0.70],
        }
        result = MODULE.analyze_door(frame, baseline, config)
        self.assertEqual(result.state, "closed")

    def test_open_door_is_detected(self) -> None:
        baseline = baseline_image()
        frame = baseline.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((128, 48, 512, 336), fill=(20, 170, 220))
        draw.line((128, 48, 512, 336), fill=(255, 255, 255), width=20)
        result = MODULE.analyze_door(frame, baseline, CONFIG)
        self.assertEqual(result.state, "open")

    def test_black_frame_is_unavailable(self) -> None:
        baseline = baseline_image()
        frame = Image.new("RGB", (640, 480), "black")
        result = MODULE.analyze_door(frame, baseline, CONFIG)
        self.assertEqual(result.state, "unavailable")


if __name__ == "__main__":
    unittest.main()
