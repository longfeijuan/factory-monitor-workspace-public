#!/usr/bin/env python3
"""Create three-frame review sheets from address-58 candidate CSV rows."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--rows-per-sheet", type=int, default=4)
    parser.add_argument("--width", type=int, default=480)
    args = parser.parse_args()

    with args.candidates.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    label_height = 30
    frame_height = round(args.width * 3 / 4)
    row_height = label_height + frame_height
    for sheet_index in range(math.ceil(len(rows) / args.rows_per_sheet)):
        group = rows[sheet_index * args.rows_per_sheet : (sheet_index + 1) * args.rows_per_sheet]
        canvas = Image.new("RGB", (args.width * 3, row_height * len(group)), "white")
        draw = ImageDraw.Draw(canvas)
        for row_index, row in enumerate(group):
            y = row_index * row_height
            draw.text(
                (6, y + 6),
                f"{row['candidate_id']}  {row['start_time_estimated']} -> {row['end_time_estimated']}",
                fill="black",
                font=font,
            )
            for column, key in enumerate(("start_image", "middle_image", "end_image")):
                image = Image.open(row[key]).convert("RGB")
                image.thumbnail((args.width, frame_height), Image.Resampling.LANCZOS)
                cell = Image.new("RGB", (args.width, frame_height), "#dddddd")
                cell.paste(image, ((args.width - image.width) // 2, (frame_height - image.height) // 2))
                canvas.paste(cell, (column * args.width, y + label_height))
        canvas.save(args.output_dir / f"sheet-{sheet_index + 1:03d}.jpg", quality=90)
    print(f"candidates={len(rows)} sheets={math.ceil(len(rows) / args.rows_per_sheet)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
