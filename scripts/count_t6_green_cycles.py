#!/usr/bin/env python3
"""Count T6 machining cycles from the tower-light green intervals."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image


def green_score(path: Path) -> float:
    # Coordinates are for the 640x360 T6 archive frames. The ROI contains only
    # the T6 tower light; green dominance cleanly separates running from idle.
    image = np.asarray(Image.open(path).convert("RGB"))[171:206, 220:254]
    red, green, blue = (image[:, :, i].astype(float) for i in range(3))
    return float(np.mean(np.maximum(0, green - np.maximum(red, blue))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threshold", type=float, default=8.0)
    parser.add_argument("--max-gap", type=float, default=10.0)
    parser.add_argument("--min-duration", type=float, default=300.0)
    args = parser.parse_args()

    paths = sorted(args.frames_dir.glob("*.jpg"))
    active: list[tuple[datetime, float, Path]] = []
    runs: list[list[tuple[datetime, float, Path]]] = []
    for path in paths:
        when = datetime.strptime(path.stem, "%Y%m%d-%H%M%S")
        score = green_score(path)
        if score <= args.threshold:
            continue
        if active and (when - active[-1][0]).total_seconds() > args.max_gap:
            runs.append(active)
            active = []
        active.append((when, score, path))
    if active:
        runs.append(active)

    rows = []
    for run in runs:
        sample_step = 5.0 if len(run) == 1 else (run[-1][0] - run[-2][0]).total_seconds()
        duration = (run[-1][0] - run[0][0]).total_seconds() + sample_step
        if duration < args.min_duration:
            continue
        rows.append(
            {
                "cycle": len(rows) + 1,
                "green_start": run[0][0].isoformat(sep=" ", timespec="seconds"),
                "green_end": run[-1][0].isoformat(sep=" ", timespec="seconds"),
                "duration_seconds": int(duration),
                "samples": len(run),
                "max_green_score": round(max(item[1] for item in run), 3),
            }
        )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["cycle"])
            writer.writeheader()
            writer.writerows(rows)
    for row in rows:
        print(
            f"{row['cycle']:02d} {row['green_start'][11:]}-{row['green_end'][11:]} "
            f"{row['duration_seconds']}s"
        )
    print(f"complete_cycles={len(rows)} frames={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
