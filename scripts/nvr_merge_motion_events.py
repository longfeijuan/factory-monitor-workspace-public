#!/usr/bin/env python3
"""Merge repeated motion-start log entries into review episodes."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--additional-input",
        action="append",
        type=Path,
        default=[],
        help="append rows from another motion-event CSV before merging",
    )
    parser.add_argument("--gap-seconds", type=float, default=60)
    parser.add_argument(
        "--bucket-seconds",
        type=int,
        help="group into fixed wall-clock buckets instead of gap-based episodes",
    )
    args = parser.parse_args()

    rows: list[dict[str, str]] = []
    for input_path in [args.input, *args.additional_input]:
        with input_path.open(encoding="utf-8", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    rows.sort(key=lambda row: (row["recorder"], int(row["channel"]), row["timestamp_local"]))

    episodes: list[dict[str, str | int]] = []
    if args.bucket_seconds:
        buckets: dict[tuple[str, int, int], list[dict[str, str]]] = {}
        for row in rows:
            when = datetime.fromisoformat(row["timestamp_local"])
            key = (
                row["recorder"],
                int(row["channel"]),
                int(when.timestamp()) // args.bucket_seconds,
            )
            buckets.setdefault(key, []).append(row)
        for bucket_rows in buckets.values():
            first = bucket_rows[0]
            last = bucket_rows[-1]
            start = datetime.fromisoformat(first["timestamp_local"])
            end = datetime.fromisoformat(last["timestamp_local"])
            episodes.append(
                {
                    "episode_id": "",
                    "gate": first["gate"],
                    "recorder": first["recorder"],
                    "channel": int(first["channel"]),
                    "start_local": first["timestamp_local"],
                    "end_local": last["timestamp_local"],
                    "trigger_count": len(bucket_rows),
                    "span_seconds": int((end - start).total_seconds()),
                }
            )
        current = None
        current_end = None
    else:
        current = None
        current_end = None
    for row in ([] if args.bucket_seconds else rows):
        when = datetime.fromisoformat(row["timestamp_local"])
        key = (row["recorder"], int(row["channel"]))
        current_key = (
            (str(current["recorder"]), int(current["channel"])) if current is not None else None
        )
        if (
            current is None
            or current_end is None
            or key != current_key
            or (when - current_end).total_seconds() > args.gap_seconds
        ):
            if current is not None:
                episodes.append(current)
            current = {
                "episode_id": "",
                "gate": row["gate"],
                "recorder": row["recorder"],
                "channel": int(row["channel"]),
                "start_local": row["timestamp_local"],
                "end_local": row["timestamp_local"],
                "trigger_count": 1,
                "span_seconds": 0,
            }
        else:
            current["end_local"] = row["timestamp_local"]
            current["trigger_count"] = int(current["trigger_count"]) + 1
            current["span_seconds"] = int(
                (when - datetime.fromisoformat(str(current["start_local"]))).total_seconds()
            )
        current_end = when
    if current is not None:
        episodes.append(current)

    episodes.sort(key=lambda row: str(row["start_local"]))
    for index, episode in enumerate(episodes, start=1):
        episode["episode_id"] = f"door-{index:06d}"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "episode_id",
                "gate",
                "recorder",
                "channel",
                "start_local",
                "end_local",
                "trigger_count",
                "span_seconds",
            ],
        )
        writer.writeheader()
        writer.writerows(episodes)
    print(
        f"events={len(rows)} episodes={len(episodes)} "
        f"gapSeconds={args.gap_seconds} bucketSeconds={args.bucket_seconds or 0}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
