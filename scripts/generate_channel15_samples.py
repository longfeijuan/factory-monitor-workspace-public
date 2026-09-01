#!/usr/bin/env python3
"""Generate channel 15 review points for day/night occupancy screening."""
import csv
from datetime import datetime, timedelta
from pathlib import Path
import sys

start = datetime.fromisoformat(sys.argv[1])
end = datetime.fromisoformat(sys.argv[2])
step = int(sys.argv[3])
output = Path(sys.argv[4])
rows = []
cursor = start
while cursor < end:
    rows.append({
        "episode_id": f"wire-15-{cursor:%Y%m%d-%H%M}",
        "gate": "三楼线割门口",
        "recorder": "nvr-main-02",
        "channel": 15,
        "start_local": cursor.isoformat(timespec="seconds"),
        "end_local": (cursor + timedelta(minutes=1)).isoformat(timespec="seconds"),
        "trigger_count": 1,
        "span_seconds": 60,
    })
    cursor += timedelta(minutes=step)
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
print(len(rows))
