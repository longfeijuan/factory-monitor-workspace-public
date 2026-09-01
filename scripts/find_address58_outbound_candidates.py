#!/usr/bin/env python3
"""Find visual-review candidates that move from factory interior to the road.

This is only a narrowing aid.  Final inclusion still requires manual review of
original frames and a clearly visible carried item.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image


def road_edge_x(y: float) -> float:
    """Approximate the user-confirmed left edge of the blue road region."""
    return 675.0 + 0.23 * y


def zone(x: float, y: float) -> str:
    if x < 470:
        return "inside"
    if x >= road_edge_x(y):
        return "outside"
    return "transition"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frame_detections", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.12)
    parser.add_argument("--max-seconds", type=float, default=24.0)
    parser.add_argument("--max-gap-seconds", type=float, default=11.0)
    parser.add_argument("--allow-direct", action="store_true", help="allow sparse keyframes to jump from inside to outside")
    args = parser.parse_args()

    frames = []
    dimensions: dict[str, tuple[int, int]] = {}
    with args.frame_detections.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            when = datetime.fromisoformat(row["event_local"]) + timedelta(
                seconds=float(row["media_offset_seconds"])
            )
            people = []
            image_path = row["image"]
            if image_path not in dimensions:
                with Image.open(image_path) as image:
                    dimensions[image_path] = image.size
            frame_width, frame_height = dimensions[image_path]
            for detection in json.loads(row["detections_json"]):
                if detection["label"] != "person" or float(detection["confidence"]) < args.min_confidence:
                    continue
                x1, y1, x2, y2 = map(float, detection["box"])
                height = y2 - y1
                width = x2 - x1
                if height < 34 or width > height * 1.4:
                    continue
                # Bottom-center approximates the person's contact point on the ground.
                # Normalize all detections to the 960x720 coordinate system in
                # which the fixed, user-confirmed gate boundary was calibrated.
                x = ((x1 + x2) / 2) * 960.0 / frame_width
                y = y2 * 720.0 / frame_height
                people.append(
                    {
                        "x": x,
                        "y": y,
                        "zone": zone(x, y),
                        "confidence": float(detection["confidence"]),
                        "box": detection["box"],
                    }
                )
            frames.append({**row, "when": when, "people": people})
    frames.sort(key=lambda row: row["when"])

    candidates = []
    used_until = frames[0]["when"] - timedelta(days=1) if frames else datetime.min
    for start_index, frame in enumerate(frames):
        if frame["when"] <= used_until:
            continue
        # The actual factory-side passage begins in the lower-left foreground.
        # Detections high in the image are road-side riders/parked-bike false
        # positives and must never seed an outbound track.
        inside_people = [
            person
            for person in frame["people"]
            if person["zone"] == "inside" and person["y"] >= 240
        ]
        for start_person in inside_people:
            tracks = [([start_index], start_person, False)]
            best = None
            for next_index in range(start_index + 1, len(frames)):
                next_frame = frames[next_index]
                elapsed = (next_frame["when"] - frame["when"]).total_seconds()
                if elapsed <= 0:
                    continue
                if elapsed > args.max_seconds:
                    break
                advanced = []
                for indexes, last_person, saw_transition in tracks:
                    last_when = frames[indexes[-1]]["when"]
                    gap = (next_frame["when"] - last_when).total_seconds()
                    # Allow a short detector miss while the person passes the
                    # bright transition strip.  The final result is still
                    # confirmed manually on the uninterrupted source clip.
                    if gap > args.max_gap_seconds:
                        continue
                    for person in next_frame["people"]:
                        distance = math.hypot(person["x"] - last_person["x"], person["y"] - last_person["y"])
                        max_distance = 55 + 75 * gap
                        if distance > max_distance:
                            continue
                        # Outbound paths move predominantly right/up; tolerate detector jitter.
                        if person["x"] < last_person["x"] - 85:
                            continue
                        if person["y"] > last_person["y"] + 100:
                            continue
                        seen = saw_transition or person["zone"] == "transition"
                        new_indexes = indexes + [next_index]
                        advanced.append((new_indexes, person, seen))
                        if (
                            person["zone"] == "outside"
                            and (seen or args.allow_direct)
                            and person["x"] - start_person["x"] >= 150
                            and start_person["y"] - person["y"] >= 40
                        ):
                            score = (
                                person["x"] - start_person["x"]
                                + 0.4 * (start_person["y"] - person["y"])
                                + 30 * len(new_indexes)
                            )
                            if best is None or score > best[0]:
                                best = (score, new_indexes, person)
                # Keep the most plausible few paths so crowds do not explode combinations.
                tracks = sorted(
                    advanced,
                    key=lambda item: (item[1]["x"], -item[1]["y"], len(item[0])),
                    reverse=True,
                )[:20]
                if not tracks and best is not None:
                    break
            if best is None:
                continue
            _, indexes, end_person = best
            transition_indexes = [
                index
                for index in indexes
                if any(person["zone"] == "transition" for person in frames[index]["people"])
            ]
            middle_index = transition_indexes[len(transition_indexes) // 2] if transition_indexes else indexes[len(indexes) // 2]
            end_index = indexes[-1]
            candidates.append(
                {
                    "candidate_id": f"cand-{len(candidates) + 1:04d}",
                    "start_time_estimated": frames[start_index]["when"].isoformat(timespec="seconds"),
                    "middle_time_estimated": frames[middle_index]["when"].isoformat(timespec="seconds"),
                    "end_time_estimated": frames[end_index]["when"].isoformat(timespec="seconds"),
                    "start_image": frame["image"],
                    "middle_image": frames[middle_index]["image"],
                    "end_image": frames[end_index]["image"],
                    "start_x": round(start_person["x"], 1),
                    "start_y": round(start_person["y"], 1),
                    "end_x": round(end_person["x"], 1),
                    "end_y": round(end_person["y"], 1),
                    "track_frames": len(indexes),
                }
            )
            used_until = frames[end_index]["when"] + timedelta(seconds=5)
            break

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "start_time_estimated",
        "middle_time_estimated",
        "end_time_estimated",
        "start_image",
        "middle_image",
        "end_image",
        "start_x",
        "start_y",
        "end_x",
        "end_y",
        "track_frames",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)
    print(f"frames={len(frames)} candidates={len(candidates)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
