#!/usr/bin/env python3
"""Generate fixed-time samples for the two Tail-2 warehouse overview cameras."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--step-minutes", type=int, default=10)
    parser.add_argument("--step-seconds", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    step = timedelta(seconds=args.step_seconds) if args.step_seconds else timedelta(minutes=args.step_minutes)
    if end <= start or step.total_seconds() <= 0:
        parser.error("invalid time range or step")

    cameras = [(57, "尾二仓-视角57"), (58, "尾二仓-视角58")]
    rows: list[dict[str, str | int]] = []
    cursor = start
    while cursor < end:
        for channel, name in cameras:
            rows.append(
                {
                    "episode_id": f"tail2-{channel}-{cursor:%Y%m%d-%H%M%S}",
                    "gate": name,
                    "recorder": "nvr-main-02",
                    "channel": channel,
                    "start_local": cursor.isoformat(timespec="seconds"),
                    "end_local": (cursor + timedelta(minutes=1)).isoformat(timespec="seconds"),
                    "trigger_count": 1,
                    "span_seconds": 60,
                }
            )
        cursor += step

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"episodes={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
