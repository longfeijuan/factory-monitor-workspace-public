#!/usr/bin/env python3
"""Combine full-archive keyframe CSVs into the door detector manifest."""

import argparse
import csv
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    ap.add_argument("inputs", nargs="+", type=Path)
    args = ap.parse_args()
    rows = []
    for source in args.inputs:
        with source.open(newline="", encoding="utf-8") as handle:
            for index, row in enumerate(csv.DictReader(handle), 1):
                when = row["timestamp_local"]
                rows.append({
                    "episode_id": f"kf-{when[:10]}-{index:06d}",
                    "gate": "58号大门",
                    "recorder": "nvr-main-02",
                    "channel": 1,
                    "event_local": when,
                    "media_offset_seconds": 0,
                    "image": row["image"],
                })
    rows.sort(key=lambda row: row["event_local"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"frames={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
