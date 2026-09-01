#!/usr/bin/env python3
"""Build close-review sheets for Liu Fanfu's two Tail-2 machines on channel 58."""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


LIU_ZONE = (0, 330, 980, 920)


def timestamp_from_episode(path: Path) -> str:
    parts = path.parent.name.rsplit("-", 2)
    return f"{parts[-2]}{parts[-1]}"


def build_sheets(paths: list[Path], output_dir: Path, group_minutes: int) -> None:
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        stamp = timestamp_from_episode(path)
        hour = int(stamp[8:10])
        minute = int(stamp[10:12])
        if group_minutes >= 60:
            key = f"{stamp[:8]}-{hour:02d}"
        else:
            bucket = minute // group_minutes * group_minutes
            key = f"{stamp[:8]}-{hour:02d}{bucket:02d}"
        groups[key].append(path)

    output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=18)
    columns, cell_w, label_h = 5, 490, 25
    crop_w = LIU_ZONE[2] - LIU_ZONE[0]
    crop_h = LIU_ZONE[3] - LIU_ZONE[1]
    cell_h = round(crop_h * cell_w / crop_w)

    for group, items in sorted(groups.items()):
        items.sort(key=timestamp_from_episode)
        rows = math.ceil(len(items) / columns)
        sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, path in enumerate(items):
            stamp = timestamp_from_episode(path)
            with Image.open(path) as frame:
                crop = frame.convert("RGB").crop(LIU_ZONE)
                crop = crop.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
            x = index % columns * cell_w
            y = index // columns * (cell_h + label_h)
            sheet.paste(crop, (x, y + label_h))
            draw.text((x + 4, y + 2), f"{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}", fill="black", font=font)
        sheet.save(output_dir / f"{group}.jpg", quality=92)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    two_minute: list[Path] = []
    for part in ("morning", "afternoon", "evening"):
        two_minute.extend(sorted((args.root / f"frames-{part}-2m").glob("tail2-58-*/frame.jpg")))
    build_sheets(two_minute, args.root / "review-liu-machine-zone-2m", 60)

    for refine_dir in sorted(args.root.glob("frames-refine-liu-*-30s")):
        paths = sorted(refine_dir.glob("tail2-58-*/frame.jpg"))
        build_sheets(paths, args.root / f"review-liu-machine-zone-{refine_dir.name.removeprefix('frames-')}", 10)

    print(f"two_minute_frames={len(two_minute)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
