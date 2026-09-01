#!/usr/bin/env python3
"""Build one chronological contact sheet per detailed door episode."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--frame-width", type=int, default=260)
    parser.add_argument(
        "--sample-step",
        type=int,
        default=1,
        help="keep every Nth snapshot when building long review sheets",
    )
    args = parser.parse_args()

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with args.snapshots.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            groups[row["episode_id"]].append(row)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    for episode_id, frames in groups.items():
        offset_key = "media_offset_seconds" if "media_offset_seconds" in frames[0] else "offset"
        frames.sort(key=lambda row: float(row[offset_key]))
        frames = frames[:: max(1, args.sample_step)]
        first_image = Image.open(frames[0]["image"])
        frame_height = round(first_image.height * args.frame_width / first_image.width)
        label_height = 22
        rows = math.ceil(len(frames) / args.columns)
        canvas = Image.new(
            "RGB",
            (args.columns * args.frame_width, rows * (frame_height + label_height)),
            "white",
        )
        for index, frame in enumerate(frames):
            image = Image.open(frame["image"]).convert("RGB")
            image = image.resize((args.frame_width, frame_height), Image.Resampling.LANCZOS)
            x = (index % args.columns) * args.frame_width
            y = (index // args.columns) * (frame_height + label_height)
            canvas.paste(image, (x, y + label_height))
            draw = ImageDraw.Draw(canvas)
            draw.text(
                (x + 4, y + 4),
                f"{episode_id}  +{float(frame[offset_key]):.1f}s",
                fill="black",
                font=font,
            )
        canvas.save(args.output_dir / f"{episode_id}-timeline.jpg", quality=88)
    print(f"episodes={len(groups)} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
