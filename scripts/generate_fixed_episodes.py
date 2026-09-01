#!/usr/bin/env python3
"""Generate fixed wall-clock review episodes for continuous NVR screening."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--recorder", default="nvr-main-02")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--gate", default="58号大门")
    parser.add_argument("--prefix", default="door")
    parser.add_argument("--index-start", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    if end <= start:
        parser.error("--end must be after --start")
    if args.minutes <= 0:
        parser.error("--minutes must be positive")

    rows = []
    cursor = start
    index = args.index_start
    while cursor < end:
        episode_end = min(end, cursor + timedelta(minutes=args.minutes))
        rows.append(
            {
                "episode_id": f"{args.prefix}-{index:06d}",
                "gate": args.gate,
                "recorder": args.recorder,
                "channel": args.channel,
                "start_local": cursor.isoformat(timespec="seconds"),
                "end_local": episode_end.isoformat(timespec="seconds"),
                "trigger_count": 1,
                "span_seconds": int((episode_end - cursor).total_seconds()),
            }
        )
        cursor = episode_end
        index += 1

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
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"episodes={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
