#!/usr/bin/env python3
"""Build a deterministic second-pass episode list from pending Gate-58 rows."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS = ("episode_id", "gate", "recorder", "channel", "start_local", "end_local")
DECISION_FIELDS = (
    "candidate_id",
    "start_side",
    "boundary_crossed",
    "end_side",
    "occluded",
    "evidence_paths",
    "review_note",
)


def build(
    input_path: Path,
    output_path: Path,
    recorder: str,
    channel: int,
    decisions_template: Path | None = None,
) -> dict:
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"candidate_id", "event_time", "final_decision"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"输入CSV缺少字段：{sorted(missing)}")
        pending = [row for row in reader if row["final_decision"] == "待复核"]

    rows = [
        {
            "episode_id": f"pending-{row['candidate_id']}",
            "gate": "大井街58号大门",
            "recorder": recorder,
            "channel": channel,
            "start_local": row["event_time"],
            "end_local": row["event_time"],
        }
        for row in pending
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if decisions_template is not None:
        decisions_template.parent.mkdir(parents=True, exist_ok=True)
        with decisions_template.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=DECISION_FIELDS)
            writer.writeheader()
            writer.writerows({"candidate_id": row["candidate_id"]} for row in pending)
    return {
        "pending": len(rows),
        "candidate_ids": [row["candidate_id"] for row in pending],
        "output": str(output_path.resolve()),
        "decisions_template": str(decisions_template.resolve()) if decisions_template else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Gate-58 pending review episodes")
    parser.add_argument("input", type=Path, help="final_events.csv")
    parser.add_argument("output", type=Path)
    parser.add_argument("--recorder", default="nvr-main-02")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--decisions-template", type=Path)
    args = parser.parse_args()
    try:
        result = build(
            args.input,
            args.output,
            args.recorder,
            args.channel,
            args.decisions_template,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
