#!/usr/bin/env python3
"""Build fixed-time contact sheets from nvr_archive_sample_frames output."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--bucket-seconds", type=int, default=60)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--frame-width", type=int, default=300)
    parser.add_argument("--crop", help="left,top,right,bottom in source pixels")
    args = parser.parse_args()

    crop = None
    if args.crop:
        crop = tuple(int(value) for value in args.crop.split(","))
        if len(crop) != 4:
            parser.error("--crop must be left,top,right,bottom")

    groups: dict[int, list[dict[str, str]]] = defaultdict(list)
    with args.frames_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            offset = float(row["offset_seconds"])
            groups[int(offset // args.bucket_seconds)].append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    for bucket, rows in sorted(groups.items()):
        rows.sort(key=lambda row: float(row["offset_seconds"]))
        first = Image.open(rows[0]["image"]).convert("RGB")
        if crop:
            first = first.crop(crop)
        frame_height = round(first.height * args.frame_width / first.width)
        label_height = 20
        sheet_rows = math.ceil(len(rows) / args.columns)
        canvas = Image.new(
            "RGB",
            (args.columns * args.frame_width, sheet_rows * (frame_height + label_height)),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for index, row in enumerate(rows):
            image = Image.open(row["image"]).convert("RGB")
            if crop:
                image = image.crop(crop)
            image = image.resize((args.frame_width, frame_height), Image.Resampling.LANCZOS)
            x = (index % args.columns) * args.frame_width
            y = (index // args.columns) * (frame_height + label_height)
            canvas.paste(image, (x, y + label_height))
            seconds = float(row["offset_seconds"])
            draw.text((x + 4, y + 4), f"+{seconds:.0f}s", fill="black", font=font)
        start = bucket * args.bucket_seconds
        canvas.save(args.output_dir / f"minute-{start:05d}.jpg", quality=88)

    print(f"sheets={len(groups)} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
