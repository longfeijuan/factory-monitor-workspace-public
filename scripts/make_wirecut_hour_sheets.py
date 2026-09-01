#!/usr/bin/env python3
"""Build one readable four-sample sheet per hour for wire-cut cross-check views."""

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
    parser.add_argument("--channels", default="9,48,61")
    parser.add_argument("--tile-width", type=int, default=480)
    args = parser.parse_args()

    channels = [int(value) for value in args.channels.split(",") if value]
    names = {9: "9 全区旧视角", 48: "48 全区新增视角", 61: "61 最新快走丝视角"}
    grouped: dict[datetime, dict[int, list[tuple[datetime, Path]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    with args.snapshots.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel = int(row["channel"])
            if channel not in channels:
                continue
            event = datetime.fromisoformat(row["event_local"])
            hour = event.replace(minute=0, second=0, microsecond=0)
            grouped[hour][channel].append((event, Path(row["image"])))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    label_height = 28
    tile_height = round(args.tile_width * 3 / 4)
    font = ImageFont.load_default()
    for hour, by_channel in sorted(grouped.items()):
        canvas = Image.new(
            "RGB",
            (args.tile_width * 4, (tile_height + label_height) * len(channels)),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for row_index, channel in enumerate(channels):
            all_entries = sorted(by_channel.get(channel, []))
            entries = [entry for entry in all_entries if entry[0].minute in {0, 15, 30, 45}]
            if len(entries) < 4:
                entries = all_entries[:: max(1, len(all_entries) // 4)][:4]
            for column_index, (event, path) in enumerate(entries):
                image = Image.open(path).convert("RGB")
                image.thumbnail((args.tile_width, tile_height), Image.Resampling.LANCZOS)
                x = column_index * args.tile_width
                y = row_index * (tile_height + label_height)
                canvas.paste(image, (x, y + label_height))
                draw.text(
                    (x + 6, y + 7),
                    f"{names.get(channel, channel)}  {event:%m-%d %H:%M}",
                    fill="black",
                    font=font,
                )
        output = args.output_dir / f"{hour:%Y%m%d-%H}.jpg"
        canvas.save(output, quality=92)
    print(f"hours={len(grouped)} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
