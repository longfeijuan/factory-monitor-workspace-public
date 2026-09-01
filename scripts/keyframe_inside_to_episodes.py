#!/usr/bin/env python3
"""Turn factory-side person keyframes into merged source-review episodes."""

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("detections", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--confidence", type=float, default=0.10)
    ap.add_argument("--before", type=float, default=15)
    ap.add_argument("--after", type=float, default=30)
    args = ap.parse_args()
    hits = []
    sizes = {}
    with args.detections.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = row["image"]
            if path not in sizes:
                with Image.open(path) as image:
                    sizes[path] = image.size
            width, height = sizes[path]
            for detection in json.loads(row["detections_json"]):
                if detection["label"] != "person" or float(detection["confidence"]) < args.confidence:
                    continue
                x1, _, x2, y2 = map(float, detection["box"])
                x = ((x1 + x2) / 2) * 960 / width
                y = y2 * 720 / height
                if x < 470 and y >= 240:
                    hits.append(datetime.fromisoformat(row["event_local"]).replace(tzinfo=None))
                    break
    merged = []
    for when in sorted(hits):
        start = when - timedelta(seconds=args.before)
        end = when + timedelta(seconds=args.after)
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    fields = ["episode_id", "gate", "recorder", "channel", "start_local", "end_local"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, (start, end) in enumerate(merged, 1):
            writer.writerow({
                "episode_id": f"inside-{index:04d}", "gate": "58号大门",
                "recorder": "nvr-main-02", "channel": 1,
                "start_local": start.isoformat(timespec="seconds"),
                "end_local": end.isoformat(timespec="seconds"),
            })
    seconds = sum((end - start).total_seconds() for start, end in merged)
    print(f"hits={len(hits)} episodes={len(merged)} seconds={seconds:.0f} output={args.output}")


if __name__ == "__main__":
    main()
