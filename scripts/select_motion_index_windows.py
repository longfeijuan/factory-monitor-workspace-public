#!/usr/bin/env python3
"""Select high-change archive windows from one-second byte indexes."""

from __future__ import annotations

import argparse
import csv
import statistics
from datetime import datetime, timedelta
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--bucket-seconds", type=int, default=10)
    parser.add_argument("--rolling-buckets", type=int, default=30)
    parser.add_argument("--ratio", type=float, default=1.8)
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--separation-seconds", type=int, default=30)
    parser.add_argument("--window-seconds", type=int, default=45)
    args = parser.parse_args()

    candidates = []
    for path in args.inputs:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        values = [int(row["bytes"]) for row in rows]
        buckets = [
            sum(values[index : index + args.bucket_seconds])
            for index in range(0, len(values), args.bucket_seconds)
        ]
        for index, total in enumerate(buckets):
            surrounding = buckets[
                max(0, index - args.rolling_buckets) : min(
                    len(buckets), index + args.rolling_buckets + 1
                )
            ]
            baseline = statistics.median(surrounding) if surrounding else 0
            ratio = total / baseline if baseline else 0
            if ratio < args.ratio:
                continue
            row_index = min(index * args.bucket_seconds, len(rows) - 1)
            when = datetime.fromisoformat(rows[row_index]["timestamp_local"])
            candidates.append((ratio, total, when))

    selected = []
    for ratio, total, when in sorted(candidates, reverse=True):
        if any(abs((when - existing[2]).total_seconds()) < args.separation_seconds for existing in selected):
            continue
        selected.append((ratio, total, when))
        if len(selected) >= args.top:
            break
    selected.sort(key=lambda item: item[2])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "episode_id",
        "gate",
        "recorder",
        "channel",
        "start_local",
        "end_local",
        "trigger_count",
        "span_seconds",
        "change_ratio",
        "bucket_bytes",
    ]
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, (ratio, total, when) in enumerate(selected, 1):
            start = when - timedelta(seconds=10)
            end = start + timedelta(seconds=args.window_seconds)
            writer.writerow(
                {
                    "episode_id": f"motion-{index:04d}",
                    "gate": "58号大门",
                    "recorder": "nvr-main-02",
                    "channel": 1,
                    "start_local": start.replace(tzinfo=None).isoformat(timespec="seconds"),
                    "end_local": end.replace(tzinfo=None).isoformat(timespec="seconds"),
                    "trigger_count": 1,
                    "span_seconds": args.window_seconds,
                    "change_ratio": round(ratio, 4),
                    "bucket_bytes": total,
                }
            )
    print(f"candidates={len(candidates)} selected={len(selected)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
