#!/usr/bin/env python3
"""Select episodes containing a person in the factory-side foreground."""

import argparse
import csv
import json
from pathlib import Path
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("frame_detections", type=Path)
    parser.add_argument("episode_scores", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--confidence", type=float, default=0.1)
    args = parser.parse_args()

    selected = set()
    with args.frame_detections.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            with Image.open(row["image"]) as image:
                width, height = image.size
            for detection in json.loads(row["detections_json"]):
                if detection["label"] != "person" or float(detection["confidence"]) < args.confidence:
                    continue
                x1, _, x2, y2 = map(float, detection["box"])
                x = ((x1 + x2) / 2) * 960 / width
                y = y2 * 720 / height
                if x < 470 and y >= 240:
                    selected.add(row["episode_id"])
                    break

    with args.episode_scores.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["episode_id"] in selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"episodes={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
