#!/usr/bin/env python3
import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("start")
parser.add_argument("end")
parser.add_argument("step_minutes", type=int)
parser.add_argument("output", type=Path)
parser.add_argument("--channel", type=int, default=30)
parser.add_argument("--gate", default="一楼简易钢件")
parser.add_argument("--exclude-weekday", type=int, action="append", default=[])
args = parser.parse_args()

start = datetime.fromisoformat(args.start)
end = datetime.fromisoformat(args.end)
step_minutes = args.step_minutes
output = args.output
rows = []
when = start
index = 1
while when < end:
    if when.weekday() not in args.exclude_weekday:
        rows.append({
            "episode_id": f"cnc-{index:03d}", "gate": args.gate, "recorder": "nvr-main-02", "channel": args.channel,
            "start_local": when.isoformat(timespec="seconds"), "end_local": (when + timedelta(minutes=1)).isoformat(timespec="seconds"),
            "trigger_count": 1, "span_seconds": 60,
        })
        index += 1
    when += timedelta(minutes=step_minutes)
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["episode_id", "gate", "recorder", "channel", "start_local", "end_local", "trigger_count", "span_seconds"])
    writer.writeheader()
    writer.writerows(rows)
