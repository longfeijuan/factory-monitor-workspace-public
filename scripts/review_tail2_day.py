#!/usr/bin/env python3
"""Build Tail-2 dual-camera review sheets and six tower-light crop sheets."""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROIS = {
    57: {
        "罗明金-1": (1310, 760, 1450, 910),
        "罗明金-2": (900, 270, 1050, 420),
        "罗明金-3": (340, 280, 500, 450),
        "罗明金-4": (210, 710, 390, 930),
    },
    58: {
        "刘凡富-1": (130, 500, 330, 710),
        "刘凡富-2": (490, 550, 700, 760),
    },
}


def iter_frames(root: Path):
    for part in ("morning", "afternoon", "evening"):
        for path in sorted((root / f"frames-{part}-2m").glob("*/frame.jpg")):
            episode = path.parent.name
            channel = int(episode.split("-")[1])
            stamp = "-".join(episode.rsplit("-", 2)[-2:])
            yield stamp, channel, path
    for path in sorted((root / "frames-morning-retry").glob("*/frame.jpg")):
        episode = path.parent.name
        channel = int(episode.split("-")[1])
        stamp = "-".join(episode.rsplit("-", 2)[-2:])
        yield stamp, channel, path


def dual_sheets(root: Path, rows: list[tuple[str, int, Path]]) -> None:
    groups: dict[str, list[tuple[str, int, Path]]] = defaultdict(list)
    for stamp, channel, path in rows:
        groups[stamp[:11]].append((stamp, channel, path))
    output_dir = root / "review-dual-hourly"
    output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=18)
    cell_w, label_h, columns = 400, 24, 4
    for hour, items in sorted(groups.items()):
        items.sort(key=lambda item: (item[0], item[1]))
        with Image.open(items[0][2]) as sample:
            cell_h = round(sample.height * cell_w / sample.width)
        row_count = math.ceil(len(items) / columns)
        sheet = Image.new("RGB", (columns * cell_w, row_count * (cell_h + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (stamp, channel, path) in enumerate(items):
            with Image.open(path) as frame:
                image = frame.convert("RGB")
                image.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            x = (index % columns) * cell_w
            y = (index // columns) * (cell_h + label_h)
            sheet.paste(image, (x, y + label_h))
            draw.text((x + 4, y + 2), f"{stamp[9:11]}:{stamp[11:13]}  ch{channel}", fill="black", font=font)
        sheet.save(output_dir / f"{hour}.jpg", quality=91)


def calibration(root: Path, rows: list[tuple[str, int, Path]]) -> None:
    output_dir = root / "machine-review"
    output_dir.mkdir(parents=True, exist_ok=True)
    for channel in (57, 58):
        _, _, path = next(item for item in rows if item[1] == channel)
        with Image.open(path) as frame:
            image = frame.convert("RGB")
        draw = ImageDraw.Draw(image)
        for x in range(0, image.width, 200):
            draw.line((x, 0, x, image.height), fill="cyan", width=2)
            draw.text((x + 3, 150), str(x), fill="cyan", stroke_fill="black", stroke_width=2)
        for y in range(0, image.height, 200):
            draw.line((0, y, image.width, y), fill="cyan", width=2)
            draw.text((3, y + 3), str(y), fill="cyan", stroke_fill="black", stroke_width=2)
        for name, box in ROIS[channel].items():
            draw.rectangle(box, outline="red", width=5)
            draw.text((box[0] + 4, box[1] + 4), name, fill="red", stroke_fill="white", stroke_width=2)
        image.save(output_dir / f"calibration-ch{channel}.jpg", quality=92)


def machine_sheets(root: Path, rows: list[tuple[str, int, Path]]) -> None:
    output_dir = root / "machine-review"
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = [item for item in rows if int(item[0][11:13]) % 10 == 0]
    font = ImageFont.load_default(size=16)
    for channel, mapping in ROIS.items():
        channel_samples = sorted((item for item in samples if item[1] == channel), key=lambda item: item[0])
        for name, box in mapping.items():
            cell_w, label_h, columns = 220, 24, 6
            crop_w, crop_h = box[2] - box[0], box[3] - box[1]
            cell_h = round(crop_h * cell_w / crop_w)
            row_count = math.ceil(len(channel_samples) / columns)
            sheet = Image.new("RGB", (columns * cell_w, row_count * (cell_h + label_h)), "white")
            draw = ImageDraw.Draw(sheet)
            for index, (stamp, _, path) in enumerate(channel_samples):
                with Image.open(path) as frame:
                    crop = frame.convert("RGB").crop(box)
                    crop = crop.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
                x = (index % columns) * cell_w
                y = (index // columns) * (cell_h + label_h)
                sheet.paste(crop, (x, y + label_h))
                draw.text((x + 4, y + 2), f"{stamp[9:11]}:{stamp[11:13]}", fill="black", font=font)
            sheet.save(output_dir / f"{name}.jpg", quality=92)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    rows = sorted(iter_frames(args.root), key=lambda item: (item[0], item[1]))
    dual_sheets(args.root, rows)
    calibration(args.root, rows)
    machine_sheets(args.root, rows)
    print(f"frames={len(rows)} root={args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
