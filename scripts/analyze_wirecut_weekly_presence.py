#!/usr/bin/env python3
"""Summarize wire-cut area staffing and PC-desk dwell from sampled frames.

This is a screening analysis.  It reports visible-area occupancy, not employee
identity, and durations are bounded by the sampling interval.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


CONFIDENCE = 0.20

# Normalized center-point ROIs calibrated against the actual 1920x1080 frames.
# Channel 15: wooden PC desk / chair on the left of the rotated scene.
# Channel 21: dual-monitor desk in the center-left foreground.
DESK_ROIS = {
    15: (0.00, 0.15, 0.47, 0.96),
    21: (0.24, 0.14, 0.61, 0.79),
}


def people(row: dict[str, str]) -> list[dict]:
    return [
        item
        for item in json.loads(row["detections_json"])
        if item["label"] == "person" and float(item["confidence"]) >= CONFIDENCE
    ]


def in_normalized_roi(person: dict, roi: tuple[float, float, float, float], width: int = 1920, height: int = 1080) -> bool:
    x1, y1, x2, y2 = person["box"]
    cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
    left, top, right, bottom = roi
    return left <= cx <= right and top <= cy <= bottom


def contiguous_runs(rows: list[dict], key: str, step_minutes: int) -> list[list[dict]]:
    runs: list[list[dict]] = []
    current: list[dict] = []
    previous: datetime | None = None
    for row in rows:
        at = datetime.fromisoformat(row["event_local"])
        positive = bool(row[key])
        continuous = previous is not None and at - previous <= timedelta(minutes=step_minutes + 1)
        if positive and (not current or continuous):
            current.append(row)
        elif positive:
            if current:
                runs.append(current)
            current = [row]
        else:
            if current:
                runs.append(current)
            current = []
        previous = at
    if current:
        runs.append(current)
    return runs


def summarize_run(run: list[dict], step_minutes: int, image_key: str) -> dict:
    start = datetime.fromisoformat(run[0]["event_local"])
    last = datetime.fromisoformat(run[-1]["event_local"])
    # Event boundaries lie between samples.  Use the observed span as the lower
    # bound and one interval more as the practical estimate.
    return {
        "date": start.date().isoformat(),
        "start_sample": run[0]["event_local"],
        "last_sample": run[-1]["event_local"],
        "samples": len(run),
        "observed_minutes_lower_bound": int((last - start).total_seconds() / 60),
        "estimated_minutes": int((last - start).total_seconds() / 60) + step_minutes,
        "start_image": run[0][image_key],
        "end_image": run[-1][image_key],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("detections", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--step-minutes", type=int, default=10)
    parser.add_argument("--channel9-baseline", type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    by_time: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    with args.detections.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            by_time[row["event_local"]][int(row["channel"])] = row

    timeline: list[dict] = []
    for event_local, group in sorted(by_time.items()):
        if not all(channel in group for channel in (8, 9, 15, 21, 48)):
            continue
        visible = {channel: people(group[channel]) for channel in (8, 9, 15, 21, 48)}
        desk15 = [p for p in visible[15] if in_normalized_roi(p, DESK_ROIS[15])]
        desk21 = [p for p in visible[21] if in_normalized_roi(p, DESK_ROIS[21])]
        c9 = len(visible[9])
        c48 = len(visible[48])
        full_area_visible = max(c9, c48)
        elsewhere = len(visible[8]) + len(visible[15]) + len(visible[21])
        shortfall = max(0, args.channel9_baseline - full_area_visible)
        timeline.append(
            {
                "event_local": event_local,
                "channel8_visible": len(visible[8]),
                "channel9_visible": c9,
                "channel15_visible": len(visible[15]),
                "channel21_visible": len(visible[21]),
                "channel48_visible": c48,
                "full_area_visible_max_9_48": full_area_visible,
                "channel15_desk": len(desk15),
                "channel21_desk": len(desk21),
                "channel9_low": shortfall > 0,
                "channel9_unexplained_low": shortfall > elsewhere,
                "channel15_desk_occupied": bool(desk15),
                "channel21_desk_occupied": bool(desk21),
                "image9": group[9]["image"],
                "image15": group[15]["image"],
                "image21": group[21]["image"],
                "image48": group[48]["image"],
            }
        )

    with (args.output / "timeline.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(timeline[0]))
        writer.writeheader()
        writer.writerows(timeline)

    per_day: dict[str, list[dict]] = defaultdict(list)
    for row in timeline:
        per_day[row["event_local"][:10]].append(row)

    outputs: dict[str, list[dict]] = {"channel9_unexplained_low": [], "channel15_desk_occupied": [], "channel21_desk_occupied": []}
    for date, rows in sorted(per_day.items()):
        for key in outputs:
            image_key = {
                "channel9_unexplained_low": "image9",
                "channel15_desk_occupied": "image15",
                "channel21_desk_occupied": "image21",
            }[key]
            for run in contiguous_runs(rows, key, args.step_minutes):
                outputs[key].append(summarize_run(run, args.step_minutes, image_key))

    for key, rows in outputs.items():
        path = args.output / f"{key}_runs.csv"
        fields = ["date", "start_sample", "last_sample", "samples", "observed_minutes_lower_bound", "estimated_minutes", "start_image", "end_image"]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    summary = {
        "sample_interval_minutes": args.step_minutes,
        "confidence": CONFIDENCE,
        "channel9_baseline_visible_people": args.channel9_baseline,
        "complete_five_channel_samples": len(timeline),
        "runs": {
            key: {
                "count": len(rows),
                "estimated_total_minutes": sum(row["estimated_minutes"] for row in rows),
                "longest_estimated_minutes": max((row["estimated_minutes"] for row in rows), default=0),
            }
            for key, rows in outputs.items()
        },
        "limitations": [
            "Counts visible people, not named employees.",
            "Low staffing across full-area channels 9 and 48 is a candidate only; people can be occluded or outside both camera views.",
            "Run boundaries are uncertain by up to one sampling interval.",
        ],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
