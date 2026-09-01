#!/usr/bin/env python3
"""Build compact dual-camera contact sheets for refined Tail-2 intervals."""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--chunk-minutes", type=int, default=10)
    args = parser.parse_args()

    groups: dict[str, list[tuple[str, int, Path]]] = defaultdict(list)
    for path in sorted(args.frames_dir.glob("*/frame.jpg")):
        episode = path.parent.name
        parts = episode.split("-")
        channel = int(parts[1])
        requested = datetime.strptime("-".join(parts[-2:]), "%Y%m%d-%H%M%S")
        minute_bucket = requested.minute // args.chunk_minutes * args.chunk_minutes
        bucket = requested.replace(minute=minute_bucket, second=0).strftime("%Y%m%d-%H%M")
        groups[bucket].append((requested.strftime("%H:%M:%S"), channel, path))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=18)
    cell_w, label_h, columns = 400, 24, 4
    for bucket, items in sorted(groups.items()):
        items.sort(key=lambda item: (item[0], item[1]))
        with Image.open(items[0][2]) as sample:
            cell_h = round(sample.height * cell_w / sample.width)
        rows = math.ceil(len(items) / columns)
        sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (stamp, channel, path) in enumerate(items):
            with Image.open(path) as frame:
                image = frame.convert("RGB")
                image.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            x = index % columns * cell_w
            y = index // columns * (cell_h + label_h)
            sheet.paste(image, (x, y + label_h))
            draw.text((x + 4, y + 2), f"{stamp} ch{channel}", fill="black", font=font)
        sheet.save(args.output_dir / f"{bucket}.jpg", quality=91)
    print({"sheets": len(groups), "frames": sum(map(len, groups.values()))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
