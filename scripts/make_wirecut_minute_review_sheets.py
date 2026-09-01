#!/usr/bin/env python3
"""Build synchronized cross-camera minute sheets for wire-cut candidate review."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--channels", default="8,9,15,21,48,61")
    parser.add_argument("--minutes-per-sheet", type=int, default=10)
    parser.add_argument("--tile-width", type=int, default=300)
    args = parser.parse_args()

    channels = [int(value) for value in args.channels.split(",") if value]
    by_time: dict[datetime, dict[int, Path]] = defaultdict(dict)
    with args.snapshots.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel = int(row["channel"])
            if channel in channels:
                by_time[datetime.fromisoformat(row["event_local"])][channel] = Path(row["image"])

    times = sorted(by_time)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    label_height = 24
    tile_height = round(args.tile_width * 9 / 16)
    font = ImageFont.load_default()
    written = 0
    for page, offset in enumerate(range(0, len(times), args.minutes_per_sheet), start=1):
        page_times = times[offset : offset + args.minutes_per_sheet]
        canvas = Image.new(
            "RGB",
            (args.tile_width * len(page_times), (tile_height + label_height) * len(channels)),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for row_index, channel in enumerate(channels):
            for column_index, event in enumerate(page_times):
                path = by_time[event].get(channel)
                x = column_index * args.tile_width
                y = row_index * (tile_height + label_height)
                draw.text((x + 5, y + 5), f"ch{channel} {event:%m-%d %H:%M}", fill="black", font=font)
                if path and path.exists():
                    image = Image.open(path).convert("RGB")
                    image.thumbnail((args.tile_width, tile_height), Image.Resampling.LANCZOS)
                    canvas.paste(image, (x, y + label_height))
        start = page_times[0]
        end = page_times[-1]
        canvas.save(
            args.output_dir / f"p{page:02d}-{start:%Y%m%d-%H%M}-{end:%H%M}.jpg",
            quality=92,
        )
        written += 1

    print(f"sheets={written} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
