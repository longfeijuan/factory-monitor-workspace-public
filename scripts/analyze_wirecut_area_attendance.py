#!/usr/bin/env python3
"""Screen wire-cut area snapshots for prolonged, unexplained staffing shortfalls.

This is deliberately a *screening* tool.  It counts visible people in each
camera frame; it does not identify people or claim that a particular person is
absent.  The output candidates need visual review of the corresponding frames.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


PERSON_CONFIDENCE = 0.25
# Channel 15 is the overhead door/desk view.  Coordinates are for the 960px
# extracted images.  The polygon deliberately covers only the PC/desk side,
# not the doorway and corridor.
PC15_ROI = (0, 170, 355, 610)  # left, top, right, bottom


def people(row: dict[str, str]) -> list[dict]:
    return [
        item
        for item in json.loads(row["detections_json"])
        if item["label"] == "person" and float(item["confidence"]) >= PERSON_CONFIDENCE
    ]


def in_pc15(person: dict) -> bool:
    x1, y1, x2, y2 = person["box"]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    left, top, right, bottom = PC15_ROI
    return left <= cx <= right and top <= cy <= bottom


def runs(slots: list[dict], key: str, minimum: int = 1) -> list[list[dict]]:
    result: list[list[dict]] = []
    current: list[dict] = []
    for slot in slots:
        if slot[key]:
            current.append(slot)
        elif current:
            if len(current) >= minimum:
                result.append(current)
            current = []
    if len(current) >= minimum:
        result.append(current)
    return result


def span(run: list[dict]) -> dict[str, object]:
    start = datetime.fromisoformat(run[0]["event_local"])
    end = datetime.fromisoformat(run[-1]["event_local"])
    return {
        "date": start.date().isoformat(),
        "start": start.isoformat(sep=" "),
        "last_sample": end.isoformat(sep=" "),
        "samples": len(run),
        # This is a lower bound between first and final observations; actual
        # boundaries can be up to one sampling interval earlier/later.
        "observed_minutes_lower_bound": int((end - start).total_seconds() / 60),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("detections", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    by_time: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    with args.detections.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            by_time[row["event_local"]][int(row["channel"])] = row

    slots: list[dict] = []
    for event_local, rows in sorted(by_time.items()):
        p8 = people(rows[8]) if 8 in rows else []
        p9 = people(rows[9]) if 9 in rows else []
        p15 = people(rows[15]) if 15 in rows else []
        p21 = people(rows[21]) if 21 in rows else []
        p48 = people(rows[48]) if 48 in rows else []
        c8, c9, c15, c21, c48 = len(p8), len(p9), len(p15), len(p21), len(p48)
        pc15 = sum(in_pc15(person) for person in p15)
        # Channels 9 and 48 are overlapping full-area views.  Taking their
        # maximum avoids counting the same person twice while reducing blind
        # spot false positives.  Channels 8, 15 and 21 are supplemental views.
        full_area_visible = max(c9, c48)
        shortfall = max(0, 3 - full_area_visible)
        elsewhere = c8 + c15 + c21
        evaluable = all(channel in rows for channel in (8, 9, 15, 21, 48))
        slots.append(
            {
                "event_local": event_local,
                "channel8_visible": c8,
                "channel9_visible": c9,
                "channel15_visible": c15,
                "channel15_pc_visible": pc15,
                "channel21_visible": c21,
                "channel48_visible": c48,
                "full_area_visible_max_9_48": full_area_visible,
                "shortfall_from_three": shortfall,
                "other_area_visible": elsewhere,
                "evaluable": evaluable,
                "unexplained_shortfall": evaluable and shortfall > elsewhere,
                "pc15_occupied": pc15 > 0,
            }
        )

    fields = list(slots[0].keys())
    with (args.output_dir / "timeline.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(slots)

    per_day: dict[str, list[dict]] = defaultdict(list)
    for slot in slots:
        per_day[slot["event_local"][:10]].append(slot)
    pc_runs: list[dict] = []
    absence_runs: list[dict] = []
    for date, day_slots in sorted(per_day.items()):
        for index, run in enumerate(runs(day_slots, "pc15_occupied"), start=1):
            pc_runs.append({"id": f"pc15-{date}-{index}", **span(run)})
        # 10-minute samples: 3 consecutive positive points have a 20-minute
        # interval between endpoints, which meets the requested threshold.
        for index, run in enumerate(runs(day_slots, "unexplained_shortfall", 3), start=1):
            absence_runs.append({"id": f"shortfall-{date}-{index}", **span(run)})

    for filename, rows in (("channel15_pc_runs.csv", pc_runs), ("unexplained_shortfall_candidates.csv", absence_runs)):
        fields = ["id", "date", "start", "last_sample", "samples", "observed_minutes_lower_bound"]
        with (args.output_dir / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    payload = {
        "method": {
            "sample_interval_minutes": 10,
            "baseline_full_area_visible_people": 3,
            "person_confidence_threshold": PERSON_CONFIDENCE,
            "absence_candidate_rule": "maximum visible count across overlapping full-area channels 9 and 48 is below 3; visible people in supplemental channels 8, 15 and 21 combined are less than the shortfall; three or more consecutive sample points",
            "identity_note": "Counts visible people only; it does not identify employees or prove an individual was absent.",
        },
        "channel15_longest_pc_observed": max(pc_runs, key=lambda item: (item["observed_minutes_lower_bound"], item["samples"]), default=None),
        "unexplained_shortfall_candidates": absence_runs,
        "slot_count": len(slots),
    }
    (args.output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
