#!/usr/bin/env python3
"""Measure the six first-floor CNC tower-light green states from fisheye frames."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "cnc-floor1-runtime-v2.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
MACHINE_ROIS = {
    machine: tuple(coordinates)
    for machine, coordinates in CONFIG["calibration"]["machine_rois"].items()
}
REFERENCE_SIZE = tuple(CONFIG["calibration"]["reference_size"])


def scaled_roi(
    roi: tuple[int, int, int, int], image_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Scale the versioned reference ROIs to the decoded frame size."""
    scale_x = image_size[0] / REFERENCE_SIZE[0]
    scale_y = image_size[1] / REFERENCE_SIZE[1]
    left, top, right, bottom = roi
    return (
        round(left * scale_x),
        round(top * scale_y),
        round(right * scale_x),
        round(bottom * scale_y),
    )


def green_metrics(image: Image.Image, roi: tuple[int, int, int, int]) -> tuple[int, float, int]:
    array = np.asarray(image.convert("RGB").crop(roi), dtype=np.int16)
    red, green, blue = (array[:, :, index] for index in range(3))
    mask = (
        (green > 95)
        & (green - red > 28)
        & (green - blue > 18)
        & (green > red * 1.20)
        & (green > blue * 1.10)
    )
    dominance = np.maximum(0, green - np.maximum(red, blue))
    return int(mask.sum()), float(np.percentile(dominance, 99.5)), int(green.max())


def is_working_time(value: datetime) -> bool:
    """Return whether a sample falls in the factory's scheduled work periods."""
    minute = value.hour * 60 + value.minute
    return (
        8 * 60 <= minute < 12 * 60
        or 13 * 60 + 30 <= minute < 17 * 60 + 30
        or 18 * 60 <= minute < 24 * 60
        or 2 * 60 <= minute < 8 * 60
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_root", type=Path)
    parser.add_argument("episodes_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--threshold", type=int, default=CONFIG["sampling"]["green_pixel_threshold"]
    )
    parser.add_argument(
        "--dominance-threshold",
        type=float,
        default=CONFIG["sampling"]["dominance_threshold"],
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.episodes_csv.open(encoding="utf-8") as handle:
        episodes = {row["episode_id"]: row for row in csv.DictReader(handle)}

    rows: list[dict[str, object]] = []
    for frame in sorted(args.frames_root.glob("*/frame.jpg")):
        episode_id = frame.parent.name
        image = Image.open(frame).convert("RGB")
        start = episodes[episode_id]["start_local"]
        start_time = datetime.fromisoformat(start)
        shift = "白班" if 8 <= start_time.hour < 20 else "夜班"
        for machine, roi in MACHINE_ROIS.items():
            pixels, dominance, maximum = green_metrics(image, scaled_roi(roi, image.size))
            rows.append({
                "episode_id": episode_id,
                "start_local": start,
                "shift": shift,
                "working": int(is_working_time(start_time)),
                "machine": machine,
                "green_pixels": pixels,
                "dominance_p995": round(dominance, 2),
                "green_max": maximum,
                "running": int(
                    pixels >= args.threshold and dominance >= args.dominance_threshold
                ),
                "image": str(frame.resolve()),
            })

    with (args.output_dir / "green-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary: list[dict[str, object]] = []
    for shift in ("白班", "夜班"):
        if not any(row["shift"] == shift for row in rows):
            continue
        for machine in MACHINE_ROIS:
            group = [
                row for row in rows
                if row["shift"] == shift and row["machine"] == machine and row["working"]
            ]
            running = sum(int(row["running"]) for row in group)
            summary.append({"shift": shift, "machine": machine, "samples": len(group), "running": running, "rate": f"{running / len(group):.1%}"})
        group = [row for row in rows if row["shift"] == shift and row["working"]]
        running = sum(int(row["running"]) for row in group)
        summary.append({"shift": shift, "machine": "六台合计", "samples": len(group), "running": running, "rate": f"{running / len(group):.1%}"})
    with (args.output_dir / "machine-rates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    first = Image.open(sorted(args.frames_root.glob("*/frame.jpg"))[0]).convert("RGB")
    draw = ImageDraw.Draw(first)
    font = ImageFont.load_default(size=32)
    for machine, reference_roi in MACHINE_ROIS.items():
        roi = scaled_roi(reference_roi, first.size)
        draw.rectangle(roi, outline="red", width=5)
        draw.text((roi[0], roi[1] - 36), machine, fill="red", font=font, stroke_width=2, stroke_fill="white")
    first.save(args.output_dir / "roi-check.jpg", quality=95)


if __name__ == "__main__":
    main()
