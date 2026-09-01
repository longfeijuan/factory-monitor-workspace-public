#!/usr/bin/env python3
"""Make review sheets from the green-pants screening JSONL outputs."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.20)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 8, 3, 8)
    rows = []
    for jsonl in args.audit_root.glob("2026-08-11-green-pants-screen-*/*.jsonl"):
        candidate_dir = jsonl.parent / "candidate_tiles"
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            index = int(item["frame"].rsplit("-", 1)[1])
            detections = [d for d in item["detections"] if d["lower_zone_pixels"] >= 250]
            if not detections:
                continue
            best = max(detections, key=lambda d: d["olive_ratio"])
            if best["olive_ratio"] < args.threshold:
                continue
            tile = candidate_dir / best["tile"]
            if tile.exists():
                rows.append((index, best["olive_ratio"], tile))
    rows.sort()
    cols, tile_w, tile_h, label_h, per_sheet = 4, 390, 390, 42, 24
    font = ImageFont.load_default(size=18)
    for page, offset in enumerate(range(0, len(rows), per_sheet), 1):
        batch = rows[offset : offset + per_sheet]
        sheet = Image.new("RGB", (cols * tile_w, 6 * (tile_h + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for pos, (index, ratio, path) in enumerate(batch):
            image = Image.open(path).convert("RGB")
            image.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
            x, y = (pos % cols) * tile_w, (pos // cols) * (tile_h + label_h)
            sheet.paste(image, (x + (tile_w - image.width) // 2, y))
            stamp = base + timedelta(minutes=(index - 1) * 10)
            draw.text((x + 8, y + tile_h + 6), f"#{index} {stamp:%m-%d %H:%M} score={ratio:.2f}", fill="black", font=font)
        sheet.save(args.output / f"candidates-{page:02d}.jpg", "JPEG", quality=90)
    print(f"candidates={len(rows)} sheets={(len(rows) + per_sheet - 1) // per_sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
