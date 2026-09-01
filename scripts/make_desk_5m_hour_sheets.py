#!/usr/bin/env python3
"""Build readable 12-sample (5-minute) hourly sheets for one desk camera."""

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
    parser.add_argument("--channel", type=int, default=15)
    parser.add_argument("--tile-width", type=int, default=480)
    args = parser.parse_args()

    grouped: dict[datetime, list[tuple[datetime, Path]]] = defaultdict(list)
    with args.snapshots.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["channel"]) != args.channel:
                continue
            event = datetime.fromisoformat(row["event_local"])
            grouped[event.replace(minute=0, second=0, microsecond=0)].append(
                (event, Path(row["image"]))
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    columns = 4
    rows = 3
    label_height = 26
    tile_height = round(args.tile_width * 9 / 16)
    font = ImageFont.load_default()
    for hour, entries in sorted(grouped.items()):
        canvas = Image.new(
            "RGB",
            (args.tile_width * columns, (tile_height + label_height) * rows),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for index, (event, path) in enumerate(sorted(entries)[:12]):
            image = Image.open(path).convert("RGB")
            image.thumbnail((args.tile_width, tile_height), Image.Resampling.LANCZOS)
            column = index % columns
            row = index // columns
            x = column * args.tile_width
            y = row * (tile_height + label_height)
            canvas.paste(image, (x, y + label_height))
            draw.text((x + 6, y + 6), f"ch{args.channel}  {event:%m-%d %H:%M}", fill="black", font=font)
        canvas.save(args.output_dir / f"{hour:%Y%m%d-%H}.jpg", quality=92)

    print(f"hours={len(grouped)} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
