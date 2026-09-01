#!/usr/bin/env python3
"""Generate synchronized read-only archive samples for wire-cut cross-check cameras."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--step-minutes", type=int, default=5)
    parser.add_argument("--step-seconds", type=int)
    parser.add_argument("--channels", default="8,9,15,21,48")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    channels = [int(value.strip()) for value in args.channels.split(",") if value.strip()]
    names = {
        8: "三楼线割扫码",
        9: "三楼线割全区",
        15: "三楼线割门口电脑位",
        21: "三楼线割办公室门口电脑位",
        48: "三楼快走丝全区新增视角",
    }

    rows = []
    cursor = start
    while cursor <= end:
        for channel in channels:
            rows.append(
                {
                    "episode_id": f"cross-{channel}-{cursor:%Y%m%d-%H%M%S}",
                    "gate": names.get(channel, f"通道{channel}"),
                    "recorder": "nvr-main-02",
                    "channel": channel,
                    "start_local": cursor.isoformat(timespec="seconds"),
                    "end_local": (cursor + timedelta(minutes=1)).isoformat(timespec="seconds"),
                    "trigger_count": 1,
                    "span_seconds": 60,
                }
            )
        if args.step_seconds:
            cursor += timedelta(seconds=args.step_seconds)
        else:
            cursor += timedelta(minutes=args.step_minutes)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(len(rows))


if __name__ == "__main__":
    main()
