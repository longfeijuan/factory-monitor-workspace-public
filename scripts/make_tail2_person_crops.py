#!/usr/bin/env python3
"""Create chronological person-crop sheets from Tail-2 YOLO screening output."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if not overlap:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return overlap / (area_a + area_b - overlap)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--confidence", type=float, default=0.45)
    parser.add_argument("--channel", type=int, default=57)
    parser.add_argument(
        "--parts",
        nargs="+",
        default=("morning", "afternoon", "evening"),
        help="Suffixes of detections-<part> directories to include.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (defaults to review-person-crops-ch<channel> under root).",
    )
    args = parser.parse_args()
    groups: dict[str, list[tuple[str, Path, list[float], float]]] = defaultdict(list)
    for part in args.parts:
        path = args.root / f"detections-{part}" / "frame_detections.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if int(row["channel"]) != args.channel:
                    continue
                detections = sorted(
                    (
                        item
                        for item in json.loads(row["detections_json"])
                        if item["label"] == "person" and float(item["confidence"]) >= args.confidence
                    ),
                    key=lambda item: float(item["confidence"]),
                    reverse=True,
                )
                kept = []
                for item in detections:
                    if all(iou(item["box"], other["box"]) < 0.45 for other in kept):
                        kept.append(item)
                hour = row["start_local"][11:13]
                for item in kept:
                    groups[hour].append(
                        (row["start_local"][11:16], Path(row["image"]), item["box"], float(item["confidence"]))
                    )

    output = args.output_dir or args.root / f"review-person-crops-ch{args.channel}"
    output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=16)
    cell_w, cell_h, label_h, columns = 180, 260, 24, 8
    for hour, items in sorted(groups.items()):
        rows = math.ceil(len(items) / columns)
        sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (stamp, path, box, score) in enumerate(items):
            with Image.open(path) as frame:
                image = frame.convert("RGB")
                x1, y1, x2, y2 = box
                w, h = x2 - x1, y2 - y1
                crop_box = (
                    max(0, int(x1 - w * 0.2)),
                    max(0, int(y1 - h * 0.15)),
                    min(image.width, int(x2 + w * 0.2)),
                    min(image.height, int(y2 + h * 0.1)),
                )
                crop = image.crop(crop_box)
                crop.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            x = (index % columns) * cell_w + (cell_w - crop.width) // 2
            y = (index // columns) * (cell_h + label_h)
            sheet.paste(crop, (x, y + label_h))
            draw.text((index % columns * cell_w + 4, y + 2), f"{stamp}  {score:.2f}", fill="black", font=font)
        sheet.save(output / f"{hour}.jpg", quality=92)
    print({"hours": len(groups), "crops": sum(map(len, groups.values()))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
