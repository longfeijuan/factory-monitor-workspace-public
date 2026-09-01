#!/usr/bin/env python3
import argparse
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FRAME_RE = re.compile(r"f\d+_(\d+(?:\.\d+)?)\.jpg$")
TIMESTAMP_RE = re.compile(r"(\d{8}-\d{6})\.jpg$")
DASH_TIMESTAMP_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.jpg$")
EPISODE_RE = re.compile(r"cnc-(\d+)$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--every", type=int, default=1)
    parser.add_argument("--crop", help="x,y,width,height")
    parser.add_argument("--name-start")
    parser.add_argument("--name-end")
    parser.add_argument("--episode-prefix", help="include frame.jpg files whose parent directory begins with this prefix")
    args = parser.parse_args()

    frames = []
    episode_start = int(args.name_start.removeprefix("cnc-")) if args.name_start else None
    episode_end = int(args.name_end.removeprefix("cnc-")) if args.name_end else None
    for path in Path(args.input_dir).rglob("*.jpg"):
        episode_match = EPISODE_RE.match(path.parent.name)
        if episode_match and path.name == "frame.jpg":
            episode = int(episode_match.group(1))
            if episode_start is not None and episode < episode_start:
                continue
            if episode_end is not None and episode > episode_end:
                continue
            frames.append((float(episode), path))
            continue
        if args.episode_prefix and path.name == "frame.jpg" and path.parent.name.startswith(args.episode_prefix):
            suffix = re.search(r"-(\d+)$", path.parent.name)
            if suffix:
                frames.append((float(suffix.group(1)), path))
            continue
        if args.name_start and path.name < args.name_start:
            continue
        if args.name_end and path.name > args.name_end:
            continue
        match = FRAME_RE.match(path.name)
        if match:
            frames.append((float(match.group(1)), path))
            continue
        match = TIMESTAMP_RE.match(path.name)
        if match:
            frames.append((float(match.group(1).replace("-", "")), path))
            continue
        match = DASH_TIMESTAMP_RE.match(path.name)
        if match:
            frames.append((float("".join(match.groups())), path))
    frames.sort()
    if not frames:
        raise SystemExit("no frames found")

    selected = []
    next_time = frames[0][0]
    for elapsed, path in frames:
        if elapsed + 0.001 >= next_time:
            selected.append((elapsed, path))
            next_time += args.interval

    label_h = 28
    font = ImageFont.load_default()
    selected = selected[::args.every]
    crop = tuple(map(int, args.crop.split(","))) if args.crop else None
    if crop and len(crop) != 4:
        raise SystemExit("crop must be x,y,width,height")

    with Image.open(selected[0][1]) as sample:
        if crop:
            _, _, crop_w, crop_h = crop
            cell_h = round(crop_h * args.width / crop_w)
        else:
            cell_h = round(sample.height * args.width / sample.width)
    rows = math.ceil(len(selected) / args.columns)
    sheet = Image.new("RGB", (args.columns * args.width, rows * (cell_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)

    for index, (elapsed, path) in enumerate(selected):
        with Image.open(path) as image:
            image = image.convert("RGB")
            if crop:
                x, y, crop_w, crop_h = crop
                image = image.crop((x, y, x + crop_w, y + crop_h))
            image.thumbnail((args.width, cell_h))
            x = (index % args.columns) * args.width
            y = (index // args.columns) * (cell_h + label_h)
            sheet.paste(image, (x, y))
            if path.name == "frame.jpg" and (EPISODE_RE.match(path.parent.name) or args.episode_prefix):
                label = path.parent.name
            else:
                label = f"+{elapsed:.1f}s  {path.name}" if FRAME_RE.match(path.name) else path.name
            draw.text((x + 8, y + cell_h + 7), label, fill="black", font=font)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, quality=90)


if __name__ == "__main__":
    main()
