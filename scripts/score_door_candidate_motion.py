#!/usr/bin/env python3
"""Add consecutive-frame visual-change scores to door person candidates."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image


GATE_ROIS = {
    41: (0.35, 0.10, 0.73, 0.88),
    64: (0.00, 0.00, 0.48, 0.60),
    5: (0.16, 0.05, 0.67, 0.88),
    1: (0.08, 0.00, 0.95, 0.95),
}


def load_gray(path: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.int16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episode_scores", type=Path)
    parser.add_argument("frame_detections", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--person-conf", type=float, default=0.25)
    parser.add_argument("--pixel-threshold", type=int, default=24)
    args = parser.parse_args()

    with args.episode_scores.open(encoding="utf-8", newline="") as handle:
        episode_rows = list(csv.DictReader(handle))
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with args.frame_detections.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            groups[row["episode_id"]].append(row)
    for group in groups.values():
        group.sort(key=lambda row: float(row["media_offset_seconds"]))

    output_rows = []
    for episode in episode_rows:
        frames = groups[episode["episode_id"]]
        gate_changes: list[float] = []
        person_changes: list[float] = []
        for left, right in zip(frames, frames[1:]):
            a = load_gray(left["image"])
            b = load_gray(right["image"])
            height, width = a.shape
            diff = np.abs(a - b)
            roi = GATE_ROIS.get(int(episode["channel"]), (0.0, 0.0, 1.0, 1.0))
            x1, y1, x2, y2 = (
                round(roi[0] * width),
                round(roi[1] * height),
                round(roi[2] * width),
                round(roi[3] * height),
            )
            gate = diff[y1:y2, x1:x2]
            gate_changes.append(float(np.mean(gate >= args.pixel_threshold)))

            mask = np.zeros_like(a, dtype=bool)
            for frame in (left, right):
                for detection in json.loads(frame["detections_json"]):
                    if detection["label"] != "person" or float(detection["confidence"]) < args.person_conf:
                        continue
                    bx1, by1, bx2, by2 = [round(float(value)) for value in detection["box"]]
                    pad_x = max(2, round((bx2 - bx1) * 0.1))
                    pad_y = max(2, round((by2 - by1) * 0.1))
                    bx1, bx2 = max(0, bx1 - pad_x), min(width, bx2 + pad_x)
                    by1, by2 = max(0, by1 - pad_y), min(height, by2 + pad_y)
                    mask[by1:by2, bx1:bx2] = True
            if mask.any():
                person_changes.append(float(np.mean(diff[mask] >= args.pixel_threshold)))
        output_rows.append(
            {
                **episode,
                "gate_change_max": round(max(gate_changes, default=0.0), 5),
                "gate_change_mean": round(float(np.mean(gate_changes)) if gate_changes else 0.0, 5),
                "person_region_change_max": round(max(person_changes, default=0.0), 5),
                "person_region_change_mean": round(
                    float(np.mean(person_changes)) if person_changes else 0.0, 5
                ),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(output_rows[0].keys())
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"episodes={len(output_rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
