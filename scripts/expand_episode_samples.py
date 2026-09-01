#!/usr/bin/env python3
"""Expand broad episode intervals into fixed-step snapshot requests."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--step-seconds", type=int, default=60)
    args = parser.parse_args()

    fields = ["episode_id", "gate", "recorder", "channel", "start_local", "end_local", "trigger_count", "span_seconds"]
    expanded = []
    for row in csv.DictReader(args.input.open(encoding="utf-8", newline="")):
        start = datetime.fromisoformat(row["start_local"])
        end = datetime.fromisoformat(row["end_local"])
        current = start
        index = 1
        while current <= end:
            expanded.append(
                {
                    "episode_id": f"{row['episode_id']}-{index:03d}",
                    "gate": row["gate"],
                    "recorder": row["recorder"],
                    "channel": row["channel"],
                    "start_local": current.isoformat(),
                    "end_local": (current + timedelta(seconds=args.step_seconds)).isoformat(),
                    "trigger_count": "1",
                    "span_seconds": str(args.step_seconds),
                }
            )
            current += timedelta(seconds=args.step_seconds)
            index += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(expanded)
    print({"episodes": len(expanded), "step_seconds": args.step_seconds})


if __name__ == "__main__":
    main()
