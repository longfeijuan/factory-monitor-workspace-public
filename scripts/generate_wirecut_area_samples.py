#!/usr/bin/env python3
import csv
from datetime import datetime, timedelta, time
from pathlib import Path
import sys

start = datetime.fromisoformat(sys.argv[1])
end = datetime.fromisoformat(sys.argv[2])
step = int(sys.argv[3])
output = Path(sys.argv[4])
channels = [
    (8, "三楼线割扫码"),
    (9, "三楼线割全区"),
    (15, "三楼线割门口"),
    (21, "三楼线割办公室门口"),
    (48, "三楼快走丝全区新增视角"),
]
rows = []
day = start.date()
while day <= end.date():
    if day.weekday() != 6:  # Sunday excluded
        cursor = datetime.combine(day, time(8, 0))
        limit = min(datetime.combine(day, time(20, 0)), end)
        while cursor < limit:
            if cursor >= start:
                for channel, name in channels:
                    rows.append({
                        "episode_id": f"wire-{channel}-{cursor:%Y%m%d-%H%M}", "gate": name,
                        "recorder": "nvr-main-02", "channel": channel,
                        "start_local": cursor.isoformat(timespec="seconds"),
                        "end_local": (cursor + timedelta(minutes=1)).isoformat(timespec="seconds"),
                        "trigger_count": 1, "span_seconds": 60,
                    })
            cursor += timedelta(minutes=step)
    day += timedelta(days=1)
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
print(len(rows))
