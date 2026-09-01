#!/usr/bin/env python3
"""Expand sparse outbound candidates into continuous source-review clips."""

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--before", type=float, default=20)
    ap.add_argument("--after", type=float, default=20)
    args = ap.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fields = ["episode_id", "gate", "recorder", "channel", "start_local", "end_local"]
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            start = datetime.fromisoformat(row["start_time_estimated"]).replace(tzinfo=None) - timedelta(seconds=args.before)
            end = datetime.fromisoformat(row["end_time_estimated"]).replace(tzinfo=None) + timedelta(seconds=args.after)
            writer.writerow({"episode_id": row["candidate_id"], "gate": "58号大门", "recorder": "nvr-main-02", "channel": 1, "start_local": start.isoformat(timespec="seconds"), "end_local": end.isoformat(timespec="seconds")})
    print(f"episodes={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
