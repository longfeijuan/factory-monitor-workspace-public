#!/usr/bin/env python3
"""Score foreground change in a fixed ROI for archive frame sequences."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--roi", required=True, help="left,top,right,bottom")
    parser.add_argument("--baseline-offset", type=float, default=0.0)
    parser.add_argument("--pixel-threshold", type=float, default=28.0)
    parser.add_argument(
        "--previous-frame",
        action="store_true",
        help="compare each frame with the previous sampled frame instead of one baseline",
    )
    args = parser.parse_args()

    roi = tuple(int(value) for value in args.roi.split(","))
    if len(roi) != 4:
        parser.error("--roi must be left,top,right,bottom")
    with args.frames_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    baseline_row = min(
        rows,
        key=lambda row: abs(float(row["offset_seconds"]) - args.baseline_offset),
    )
    baseline = np.asarray(
        Image.open(baseline_row["image"]).convert("L").crop(roi), dtype=np.float32
    )

    scored = []
    previous = baseline
    for row in rows:
        image = np.asarray(Image.open(row["image"]).convert("L").crop(roi), dtype=np.float32)
        reference = previous if args.previous_frame else baseline
        difference = np.abs(image - reference)
        mask = difference >= args.pixel_threshold
        ys, xs = np.nonzero(mask)
        left, top, _, _ = roi
        scored.append(
            {
                "offset_seconds": row["offset_seconds"],
                "mean_abs_diff": round(float(difference.mean()), 4),
                "changed_fraction": round(float(mask.mean()), 6),
                "changed_centroid_x": round(float(xs.mean() + left), 2) if len(xs) else "",
                "changed_centroid_y": round(float(ys.mean() + top), 2) if len(ys) else "",
                "changed_bbox_left": int(xs.min() + left) if len(xs) else "",
                "changed_bbox_top": int(ys.min() + top) if len(ys) else "",
                "changed_bbox_right": int(xs.max() + left) if len(xs) else "",
                "changed_bbox_bottom": int(ys.max() + top) if len(ys) else "",
                "image": row["image"],
            }
        )
        previous = image

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "offset_seconds",
                "mean_abs_diff",
                "changed_fraction",
                "changed_centroid_x",
                "changed_centroid_y",
                "changed_bbox_left",
                "changed_bbox_top",
                "changed_bbox_right",
                "changed_bbox_bottom",
                "image",
            ],
        )
        writer.writeheader()
        writer.writerows(scored)
    values = np.asarray([row["changed_fraction"] for row in scored])
    print(
        {
            "frames": len(scored),
            "p50": round(float(np.percentile(values, 50)), 6),
            "p75": round(float(np.percentile(values, 75)), 6),
            "p90": round(float(np.percentile(values, 90)), 6),
            "p95": round(float(np.percentile(values, 95)), 6),
            "max": round(float(values.max()), 6),
            "output": str(args.output_csv),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
