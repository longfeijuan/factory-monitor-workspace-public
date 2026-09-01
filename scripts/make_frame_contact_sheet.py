#!/usr/bin/env python3
"""Build a chronological contact sheet from snapshot episode directories."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--frame-width", type=int, default=480)
    parser.add_argument("--crop", help="left,top,right,bottom in source pixels")
    parser.add_argument("--start-index", type=int)
    parser.add_argument("--end-index", type=int)
    args = parser.parse_args()

    paths = sorted(args.frames_dir.glob("*/frame.jpg"))
    if args.start_index is not None:
        paths = [p for p in paths if int(p.parent.name.rsplit("-", 1)[-1]) >= args.start_index]
    if args.end_index is not None:
        paths = [p for p in paths if int(p.parent.name.rsplit("-", 1)[-1]) <= args.end_index]
    if not paths:
        parser.error(f"no frame.jpg files under {args.frames_dir}")
    crop = tuple(int(value) for value in args.crop.split(",")) if args.crop else None
    if crop and len(crop) != 4:
        parser.error("--crop must be left,top,right,bottom")

    first = Image.open(paths[0]).convert("RGB")
    if crop:
        first = first.crop(crop)
    frame_height = round(first.height * args.frame_width / first.width)
    label_height = 24
    rows = math.ceil(len(paths) / args.columns)
    canvas = Image.new(
        "RGB",
        (args.columns * args.frame_width, rows * (frame_height + label_height)),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for index, path in enumerate(paths):
        image = Image.open(path).convert("RGB")
        if crop:
            image = image.crop(crop)
        image.thumbnail((args.frame_width, frame_height), Image.Resampling.LANCZOS)
        x = (index % args.columns) * args.frame_width
        y = (index // args.columns) * (frame_height + label_height)
        canvas.paste(image, (x, y + label_height))
        draw.text((x + 4, y + 6), path.parent.name, fill="black", font=font)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output, quality=90)
    print(f"frames={len(paths)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
