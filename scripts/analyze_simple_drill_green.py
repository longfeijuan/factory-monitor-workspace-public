#!/usr/bin/env python3
"""Measure five second-floor simple drilling/tapping machine green lights."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# Native camera frames are 2560x1440. Numbering follows the user's marked-up
# reference image. The tight ROIs prevent one lamp's green reflection from
# being counted as the adjacent machine.
MACHINES = {
    "1": {"roi": (1870, 780, 2040, 960), "threshold": 500},
    "2": {"roi": (1970, 250, 2070, 370), "threshold": 180},
    "3": {"roi": (1740, 90, 1820, 165), "threshold": 180},
    "4": {"roi": (1670, 155, 1740, 215), "threshold": 180},
    "5": {"roi": (1380, 270, 1580, 420), "threshold": 500},
}


def green_pixels(image: Image.Image, roi: tuple[int, int, int, int]) -> int:
    pixels = np.asarray(image.convert("RGB").crop(roi), dtype=np.int16)
    red, green, blue = (pixels[:, :, channel] for channel in range(3))
    mask = (
        (green > 110)
        & (green - red > 35)
        & (green - blue > 25)
        & (green > red * 1.25)
        & (green > blue * 1.15)
    )
    return int(mask.sum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames_root", type=Path)
    parser.add_argument("episodes_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.episodes_csv.open(encoding="utf-8", newline="") as handle:
        episodes = {row["episode_id"]: row for row in csv.DictReader(handle)}

    rows: list[dict[str, object]] = []
    frames = sorted(args.frames_root.glob("*/frame.jpg"))
    for frame_path in frames:
        episode_id = frame_path.parent.name
        if episode_id not in episodes:
            continue
        image = Image.open(frame_path).convert("RGB")
        for machine, config in MACHINES.items():
            count = green_pixels(image, config["roi"])
            rows.append(
                {
                    "episode_id": episode_id,
                    "sample_time": episodes[episode_id]["start_local"],
                    "machine": machine,
                    "green_pixels": count,
                    "threshold": config["threshold"],
                    "running": int(count >= config["threshold"]),
                    "image": str(frame_path.resolve()),
                }
            )

    with (args.output_dir / "green-metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    rates = []
    for machine in MACHINES:
        group = [row for row in rows if row["machine"] == machine]
        running = sum(int(row["running"]) for row in group)
        rates.append(
            {
                "machine": machine,
                "valid_samples": len(group),
                "running_samples": running,
                "stopped_samples": len(group) - running,
                "running_rate": round(running / len(group), 4),
            }
        )
    total_running = sum(int(row["running"]) for row in rows)
    rates.append(
        {
            "machine": "五台综合",
            "valid_samples": len(rows),
            "running_samples": total_running,
            "stopped_samples": len(rows) - total_running,
            "running_rate": round(total_running / len(rows), 4),
        }
    )
    with (args.output_dir / "machine-rates.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rates[0]))
        writer.writeheader()
        writer.writerows(rates)

    # Draw calibrated ROIs on one real frame for review.
    analyzed_frames = [path for path in frames if path.parent.name in episodes]
    check = Image.open(analyzed_frames[len(analyzed_frames) // 3]).convert("RGB")
    draw = ImageDraw.Draw(check)
    font = ImageFont.load_default(size=34)
    for machine, config in MACHINES.items():
        roi = config["roi"]
        draw.rectangle(roi, outline="red", width=5)
        draw.text((roi[0], max(0, roi[1] - 38)), machine, fill="red", font=font, stroke_width=2, stroke_fill="white")
    check.save(args.output_dir / "roi-check.jpg", quality=95)

    summary = {
        "frames": len(analyzed_frames),
        "expected_frames": len(episodes),
        "coverage_rate": round(len(analyzed_frames) / len(episodes), 4),
        "sample_interval_minutes": 1,
        "rates": rates,
        "method": "green tower-light pixels in five user-identified ROIs",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
