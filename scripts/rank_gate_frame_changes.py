#!/usr/bin/env python3
"""Select the largest consecutive visual changes from sampled gate frames."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image


def gray(path: str, width: int = 192) -> np.ndarray:
    image = Image.open(path).convert("L")
    height = round(image.height * width / image.width)
    return np.asarray(image.resize((width, height), Image.Resampling.BILINEAR), dtype=np.int16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--per-episode", type=int, default=24)
    parser.add_argument("--pixel-threshold", type=int, default=24)
    args = parser.parse_args()

    with args.snapshots.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = reader.fieldnames
    assert fields

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["episode_id"]].append(row)

    selected: list[dict[str, str]] = []
    for group in groups.values():
        group.sort(key=lambda row: float(row["media_offset_seconds"]))
        scores: list[tuple[float, dict[str, str]]] = []
        previous = None
        for row in group:
            current = gray(row["image"])
            if previous is None:
                score = 0.0
            else:
                diff = np.abs(current - previous)
                score = float(np.mean(diff >= args.pixel_threshold))
            previous = current
            scores.append((score, row))
        keep = sorted(scores, key=lambda item: item[0], reverse=True)[: args.per_episode]
        keep.sort(key=lambda item: float(item[1]["media_offset_seconds"]))
        selected.extend(row for _, row in keep)

    selected.sort(key=lambda row: (row["event_local"], float(row["media_offset_seconds"])))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)
    print(f"episodes={len(groups)} selected={len(selected)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
