#!/usr/bin/env python3
"""Build a chronological contact sheet from a flat directory of images."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--glob", default="*.jpg")
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--frame-width", type=int, default=400)
    parser.add_argument("--crop", help="left,top,right,bottom in source pixels")
    args = parser.parse_args()

    paths = sorted(args.frames_dir.glob(args.glob))
    if not paths:
        parser.error(f"no images matching {args.glob!r} under {args.frames_dir}")
    crop = tuple(int(value) for value in args.crop.split(",")) if args.crop else None
    if crop and len(crop) != 4:
        parser.error("--crop must be left,top,right,bottom")

    first = Image.open(paths[0]).convert("RGB")
    if crop:
        first = first.crop(crop)
    frame_height = round(first.height * args.frame_width / first.width)
    label_height = 22
    rows = math.ceil(len(paths) / args.columns)
    canvas = Image.new(
        "RGB",
        (args.columns * args.frame_width, rows * (frame_height + label_height)),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for index, path in enumerate(paths):
        frame = Image.open(path).convert("RGB")
        if crop:
            frame = frame.crop(crop)
        frame = frame.resize((args.frame_width, frame_height), Image.Resampling.LANCZOS)
        x = (index % args.columns) * args.frame_width
        y = (index // args.columns) * (frame_height + label_height)
        canvas.paste(frame, (x, y + label_height))
        draw.text((x + 4, y + 5), path.stem, fill="black", font=font)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output, "JPEG", quality=90)
    print(f"frames={len(paths)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
