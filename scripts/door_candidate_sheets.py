#!/usr/bin/env python3
"""Create compact visual-review sheets for person-at-gate candidates."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


CARRY_LABELS = {
    "backpack",
    "umbrella",
    "handbag",
    "suitcase",
    "bottle",
    "cup",
    "sports ball",
    "skateboard",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episode_scores", type=Path)
    parser.add_argument("frame_detections", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--min-gate-frames-025", type=int, default=2)
    parser.add_argument("--min-gate-score", type=float, default=0.25)
    parser.add_argument("--min-person-change", type=float, default=0.0)
    parser.add_argument("--min-gate-change", type=float, default=0.0)
    parser.add_argument("--min-carry-score", type=float, default=0.0)
    parser.add_argument("--gate", action="append", dest="gates")
    parser.add_argument("--episodes-per-sheet", type=int, default=5)
    parser.add_argument("--frame-width", type=int, default=360)
    args = parser.parse_args()

    with args.episode_scores.open(encoding="utf-8", newline="") as handle:
        scores = list(csv.DictReader(handle))
    selected = [
        row
        for row in scores
        if int(row["gate_person_frames_025"]) >= args.min_gate_frames_025
        and float(row["gate_person_max_score"]) >= args.min_gate_score
        and float(row.get("person_region_change_max", 0.0)) >= args.min_person_change
        and float(row.get("gate_change_max", 0.0)) >= args.min_gate_change
        and float(row.get("carry_object_max_score", 0.0)) >= args.min_carry_score
        and (not args.gates or row["gate"] in args.gates)
    ]
    selected.sort(key=lambda row: row["event_local"])
    selected_ids = {row["episode_id"] for row in selected}

    frames: dict[str, list[dict[str, str]]] = defaultdict(list)
    with args.frame_detections.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["episode_id"] in selected_ids:
                frames[row["episode_id"]].append(row)
    for group in frames.values():
        group.sort(key=lambda row: float(row["media_offset_seconds"]))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    label_height = 34
    row_height = label_height + round(args.frame_width * 9 / 16)
    sheet_width = args.frame_width * 3
    sheet_height = row_height * args.episodes_per_sheet
    index_rows = []

    for sheet_index in range(math.ceil(len(selected) / args.episodes_per_sheet)):
        group = selected[
            sheet_index * args.episodes_per_sheet : (sheet_index + 1) * args.episodes_per_sheet
        ]
        canvas = Image.new("RGB", (sheet_width, sheet_height), "white")
        draw = ImageDraw.Draw(canvas)
        for row_index, episode in enumerate(group):
            top = row_index * row_height
            label = (
                f"{episode['episode_id']}  {episode['gate']}  {episode['event_local']}  "
                f"gate={float(episode['gate_person_max_score']):.2f}  "
                f"frames={episode['gate_person_frames_025']}/{episode['frame_count']}"
            )
            draw.text((6, top + 5), label, fill="black", font=font)
            episode_frames = frames[episode["episode_id"]]
            if episode_frames:
                peak_index = max(
                    range(len(episode_frames)),
                    key=lambda index: float(episode_frames[index].get("carry_object_score", 0.0)),
                )
                selected_indexes = sorted(
                    {
                        max(0, peak_index - 2),
                        peak_index,
                        min(len(episode_frames) - 1, peak_index + 2),
                    }
                )
                selected_frames = [episode_frames[index] for index in selected_indexes]
            else:
                selected_frames = []
            for frame_index, frame in enumerate(selected_frames):
                image = Image.open(frame["image"]).convert("RGB")
                image_draw = ImageDraw.Draw(image)
                for detection in json.loads(frame["detections_json"]):
                    if float(detection["confidence"]) < 0.10:
                        continue
                    if detection["label"] == "person":
                        image_draw.rectangle(detection["box"], outline="red", width=3)
                    elif detection["label"] in CARRY_LABELS:
                        image_draw.rectangle(detection["box"], outline="yellow", width=3)
                target_height = row_height - label_height
                image.thumbnail((args.frame_width, target_height), Image.Resampling.LANCZOS)
                cell = Image.new("RGB", (args.frame_width, target_height), "#dddddd")
                x = (args.frame_width - image.width) // 2
                y = (target_height - image.height) // 2
                cell.paste(image, (x, y))
                cell_draw = ImageDraw.Draw(cell)
                cell_draw.text(
                    (5, 5),
                    f"+{float(frame['media_offset_seconds']):.1f}s  "
                    f"p={float(frame['gate_person_score']):.2f}  "
                    f"carry={float(frame.get('carry_object_score', 0.0)):.2f}",
                    fill="yellow",
                    stroke_width=2,
                    stroke_fill="black",
                    font=font,
                )
                canvas.paste(cell, (frame_index * args.frame_width, top + label_height))
            index_rows.append(
                {
                    **episode,
                    "sheet": f"sheet-{sheet_index + 1:03d}.jpg",
                    "sheet_row": row_index + 1,
                }
            )
        canvas.crop((0, 0, sheet_width, len(group) * row_height)).save(
            args.output_dir / f"sheet-{sheet_index + 1:03d}.jpg", quality=88
        )

    fields = list(selected[0].keys()) + ["sheet", "sheet_row"] if selected else []
    if fields:
        with (args.output_dir / "index.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(index_rows)
    print(f"candidates={len(selected)} sheets={math.ceil(len(selected) / args.episodes_per_sheet)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
