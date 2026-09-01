#!/usr/bin/env python3
"""Crop overlapping operator-scale tiles from the five-machine camera."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


REGIONS = {
    "left_front": (0.00, 0.32, 0.43, 1.00),
    "center_left": (0.25, 0.22, 0.66, 0.96),
    "back_aisle": (0.43, 0.05, 0.84, 0.76),
    "right_front": (0.56, 0.22, 1.00, 1.00),
    "upper_machines": (0.20, 0.00, 0.73, 0.62),
}


def sliding_regions(width: int, height: int, tile_size: int = 520, step: int = 360):
    """Yield smaller overlapping crops so distant people fill enough of a Vision input."""
    xs = list(range(0, max(1, width - tile_size + 1), step))
    ys = list(range(0, max(1, height - tile_size + 1), step))
    if not xs or xs[-1] != max(0, width - tile_size):
        xs.append(max(0, width - tile_size))
    if not ys or ys[-1] != max(0, height - tile_size):
        ys.append(max(0, height - tile_size))
    for y in ys:
        for x in xs:
            yield f"grid_{x}_{y}", (x, y, min(width, x + tile_size), min(height, y + tile_size))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--sliding", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for image_path in args.images:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        stem = image_path.parent.name if image_path.name == "frame.jpg" else image_path.stem
        if args.sliding:
            regions = sliding_regions(width, height)
        else:
            regions = (
                (name, (round(width * x1), round(height * y1), round(width * x2), round(height * y2)))
                for name, (x1, y1, x2, y2) in REGIONS.items()
            )
        for name, box in regions:
            path = args.output / f"{stem}__{name}.jpg"
            image.crop(box).save(path, "JPEG", quality=90, optimize=True)
            rows.append(f"{image_path}\t{name}\t{box}\t{path}")
    (args.output / "tiles.tsv").write_text("source\tregion\tbox\ttile\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"images={len(args.images)} tiles={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
