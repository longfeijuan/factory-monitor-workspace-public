#!/usr/bin/env python3
"""Build contact sheets for dense, minute-by-minute desk review frames."""

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
    parser.add_argument("--tile-width", type=int, default=400)
    parser.add_argument("--per-sheet", type=int, default=20)
    args = parser.parse_args()

    grouped: dict[tuple[int, str], list[tuple[datetime, Path]]] = defaultdict(list)
    with args.snapshots.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            event = datetime.fromisoformat(row["event_local"])
            grouped[(int(row["channel"]), event.strftime("%Y%m%d-%H"))].append(
                (event, Path(row["image"]))
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    columns = 5
    label_height = 28
    tile_height = round(args.tile_width * 9 / 16)
    font = ImageFont.load_default()
    written = 0

    for (channel, hour), entries in sorted(grouped.items()):
        entries.sort()
        for part, offset in enumerate(range(0, len(entries), args.per_sheet), start=1):
            chunk = entries[offset : offset + args.per_sheet]
            rows = (len(chunk) + columns - 1) // columns
            canvas = Image.new(
                "RGB",
                (args.tile_width * columns, (tile_height + label_height) * rows),
                "white",
            )
            draw = ImageDraw.Draw(canvas)
            for index, (event, path) in enumerate(chunk):
                image = Image.open(path).convert("RGB")
                image.thumbnail((args.tile_width, tile_height), Image.Resampling.LANCZOS)
                column = index % columns
                row = index // columns
                x = column * args.tile_width
                y = row * (tile_height + label_height)
                canvas.paste(image, (x, y + label_height))
                draw.text(
                    (x + 6, y + 7),
                    f"ch{channel}  {event:%m-%d %H:%M}",
                    fill="black",
                    font=font,
                )
            canvas.save(args.output_dir / f"ch{channel}-{hour}-p{part}.jpg", quality=94)
            written += 1

    print(f"sheets={written} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
