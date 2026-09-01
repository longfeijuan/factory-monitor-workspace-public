#!/usr/bin/env python3
"""Screen five-machine snapshots for the black-shirt/olive-pants operator.

This is a candidate generator, not a final attendance decision.  It tiles each
wide CCTV frame so Apple's person segmentation can see distant workers, then
scores only the lower part of each person mask for olive/green trousers.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from make_five_machine_tiles import sliding_regions


def olive_ratio(tile: Path, mask_path: Path) -> tuple[float, int]:
    image = np.asarray(Image.open(tile).convert("RGB"))
    mask_image = Image.open(mask_path).resize((image.shape[1], image.shape[0]))
    mask = np.asarray(mask_image) >= 96
    ys, _ = np.where(mask)
    if not len(ys):
        return 0.0, 0
    y0, y1 = int(ys.min()), int(ys.max())
    yy = np.indices(mask.shape)[0]
    zone = mask & (yy >= y0 + (y1 - y0) * 0.55) & (yy < y0 + (y1 - y0) * 0.90)
    zone_n = int(zone.sum())
    if zone_n == 0:
        return 0.0, 0
    rgb = image.astype(np.int16)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    olive = (
        (red > 28)
        & (red < 180)
        & (green > 28)
        & (green < 170)
        & (blue > 15)
        & (blue < 130)
        & (blue < green * 0.90)
        & (np.abs(red - green) < 45)
    )
    return float((zone & olive).sum() / zone_n), zone_n


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-root", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--vision-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    candidates_dir = args.output / "candidate_tiles"
    candidates_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"green-pants-{args.start}-{args.end}-") as raw_tmp:
        tmp = Path(raw_tmp)
        tiles_dir, masks_dir = tmp / "tiles", tmp / "masks"
        tiles_dir.mkdir(); masks_dir.mkdir()
        tile_paths: list[Path] = []
        for index in range(args.start, args.end + 1):
            frame = args.frames_root / f"five-machines-{index:06d}" / "frame.jpg"
            if not frame.exists():
                continue
            image = Image.open(frame).convert("RGB")
            width, height = image.size
            for name, box in sliding_regions(width, height):
                path = tiles_dir / f"five-machines-{index:06d}__{name}.jpg"
                image.crop(box).save(path, "JPEG", quality=88, optimize=True)
                tile_paths.append(path)

        observations: list[dict] = []
        env = dict(os.environ, MASK_OUTPUT_DIR=str(masks_dir))
        for offset in range(0, len(tile_paths), 180):
            batch = tile_paths[offset : offset + 180]
            proc = subprocess.run(
                [str(args.vision_bin), *map(str, batch)],
                check=True, capture_output=True, text=True, env=env,
            )
            for line in proc.stdout.splitlines():
                row = json.loads(line)
                if row.get("pixels", 0) and row.get("maskPath"):
                    observations.append(row)

        by_frame: dict[str, list[dict]] = {}
        for row in observations:
            tile = Path(row["path"])
            ratio, zone_n = olive_ratio(tile, Path(row["maskPath"]))
            frame_id = tile.stem.split("__", 1)[0]
            result = {
                "tile": tile.name,
                "person_pixels": row["pixels"],
                "mask_ratio": row["ratio"],
                "olive_ratio": ratio,
                "lower_zone_pixels": zone_n,
                "bbox": row["bbox"],
            }
            by_frame.setdefault(frame_id, []).append(result)
            if ratio >= 0.25 and zone_n >= 250:
                shutil.copy2(tile, candidates_dir / tile.name)

        out = args.output / f"green-pants-{args.start:06d}-{args.end:06d}.jsonl"
        with out.open("w", encoding="utf-8") as handle:
            for index in range(args.start, args.end + 1):
                frame_id = f"five-machines-{index:06d}"
                detections = sorted(
                    by_frame.get(frame_id, []), key=lambda x: x["olive_ratio"], reverse=True
                )
                handle.write(json.dumps({"frame": frame_id, "detections": detections}, ensure_ascii=False) + "\n")
        print(json.dumps({
            "start": args.start, "end": args.end, "tiles": len(tile_paths),
            "person_observations": len(observations), "output": str(out),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
