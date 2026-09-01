#!/usr/bin/env python3
"""Run a first-pass person/carry-object detector over extracted door frames."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw
from ultralytics import YOLO


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

# Normalized review zones for the physical passage, not the whole camera view.
GATE_ROIS = {
    41: (0.35, 0.10, 0.73, 0.88),  # 厂房后门：中央卷帘门与门内通道
    64: (0.00, 0.00, 0.48, 0.60),  # 后门区右：左上方出口与其前方通道
    5: (0.16, 0.05, 0.67, 0.88),   # 一楼侧门：正面双开门
    1: (0.08, 0.00, 0.95, 0.95),   # 一楼大门口：画面主体通道
}


def expanded_contains(person: list[float], obj: list[float]) -> bool:
    px1, py1, px2, py2 = person
    ox1, oy1, ox2, oy2 = obj
    width = px2 - px1
    height = py2 - py1
    cx = (ox1 + ox2) / 2
    cy = (oy1 + oy2) / 2
    return px1 - width * 0.45 <= cx <= px2 + width * 0.45 and py1 - height * 0.2 <= cy <= py2 + height * 0.35


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--model", default="/tmp/codex-door-model/yolo11n.pt")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--batch", type=int, default=24)
    parser.add_argument("--annotate-score", type=float, default=0.25)
    parser.add_argument(
        "--all-classes",
        action="store_true",
        help="also detect portable COCO objects; person-only is faster and is the default",
    )
    args = parser.parse_args()

    with args.snapshots.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    paths = [row["image"] for row in rows]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model = YOLO(args.model)
    frame_rows: list[dict[str, str | int | float]] = []
    episodes: dict[str, list[dict[str, str | int | float]]] = defaultdict(list)
    chunk_size = args.batch
    for offset in range(0, len(paths), chunk_size):
        chunk_rows = rows[offset : offset + chunk_size]
        predictions = model.predict(
            paths[offset : offset + chunk_size],
            imgsz=args.imgsz,
            conf=args.conf,
            classes=None if args.all_classes else [0],
            batch=args.batch,
            device=args.device,
            verbose=False,
        )
        for row, result in zip(chunk_rows, predictions):
            detections = []
            persons: list[tuple[float, list[float]]] = []
            for box, confidence, class_id in zip(
                result.boxes.xyxy, result.boxes.conf, result.boxes.cls
            ):
                label = result.names[int(class_id)]
                coords = [round(float(value), 2) for value in box]
                score = round(float(confidence), 5)
                detections.append({"label": label, "confidence": score, "box": coords})
                if label == "person":
                    persons.append((score, coords))
            person_score = max((score for score, _ in persons), default=0.0)
            height, width = result.orig_shape
            roi = GATE_ROIS.get(int(row["channel"]), (0.0, 0.0, 1.0, 1.0))
            gate_persons = []
            for score, box in persons:
                center_x = ((box[0] + box[2]) / 2) / width
                center_y = ((box[1] + box[3]) / 2) / height
                if roi[0] <= center_x <= roi[2] and roi[1] <= center_y <= roi[3]:
                    gate_persons.append((score, box))
            gate_person_score = max((score for score, _ in gate_persons), default=0.0)
            carry_score = 0.0
            carry_labels: set[str] = set()
            for detection in detections:
                if detection["label"] not in CARRY_LABELS:
                    continue
                if any(expanded_contains(person_box, detection["box"]) for _, person_box in persons):
                    carry_score = max(carry_score, float(detection["confidence"]))
                    carry_labels.add(str(detection["label"]))
            output = {
                **row,
                "person_score": round(person_score, 5),
                "person_count": len(persons),
                "gate_person_score": round(gate_person_score, 5),
                "gate_person_count": len(gate_persons),
                "carry_object_score": round(carry_score, 5),
                "carry_labels": ",".join(sorted(carry_labels)),
                "detections_json": json.dumps(
                    detections, ensure_ascii=False, separators=(",", ":")
                ),
            }
            frame_rows.append(output)
            episodes[row["episode_id"]].append(output)

            if person_score >= args.annotate_score:
                image = Image.open(row["image"]).convert("RGB")
                draw = ImageDraw.Draw(image)
                for detection in detections:
                    if (
                        detection["label"] != "person"
                        and detection["label"] not in CARRY_LABELS
                    ):
                        continue
                    color = "red" if detection["label"] == "person" else "yellow"
                    draw.rectangle(detection["box"], outline=color, width=3)
                    draw.text(
                        (detection["box"][0], max(0, detection["box"][1] - 12)),
                        f"{detection['label']} {detection['confidence']:.2f}",
                        fill=color,
                    )
                image.save(
                    args.output_dir / f"{row['episode_id']}_{Path(row['image']).name}",
                    quality=88,
                )
        print(
            json.dumps(
                {"processedFrames": min(offset + chunk_size, len(paths)), "totalFrames": len(paths)},
                ensure_ascii=False,
            ),
            flush=True,
        )

    frame_fields = list(rows[0].keys()) + [
        "person_score",
        "person_count",
        "gate_person_score",
        "gate_person_count",
        "carry_object_score",
        "carry_labels",
        "detections_json",
    ]
    with (args.output_dir / "frame_detections.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=frame_fields)
        writer.writeheader()
        writer.writerows(frame_rows)

    episode_rows = []
    for episode_id, group in episodes.items():
        first = group[0]
        scores = [float(item["person_score"]) for item in group]
        gate_scores = [float(item["gate_person_score"]) for item in group]
        carry_scores = [float(item["carry_object_score"]) for item in group]
        episode_rows.append(
            {
                "episode_id": episode_id,
                "gate": first["gate"],
                "recorder": first["recorder"],
                "channel": first["channel"],
                "event_local": first.get("event_local") or first.get("start_local", ""),
                "person_max_score": round(max(scores), 5),
                "person_frames_010": sum(score >= 0.10 for score in scores),
                "person_frames_025": sum(score >= 0.25 for score in scores),
                "gate_person_max_score": round(max(gate_scores), 5),
                "gate_person_frames_010": sum(score >= 0.10 for score in gate_scores),
                "gate_person_frames_025": sum(score >= 0.25 for score in gate_scores),
                "carry_object_max_score": round(max(carry_scores), 5),
                "frame_count": len(group),
            }
        )
    episode_rows.sort(key=lambda item: item["event_local"])
    with (args.output_dir / "episode_scores.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(episode_rows[0].keys()))
        writer.writeheader()
        writer.writerows(episode_rows)

    print(
        json.dumps(
            {
                "frames": len(frame_rows),
                "episodes": len(episode_rows),
                "episodesPerson025": sum(float(row["person_max_score"]) >= 0.25 for row in episode_rows),
                "episodesGatePerson025": sum(
                    float(row["gate_person_max_score"]) >= 0.25 for row in episode_rows
                ),
                "episodesCarry010": sum(float(row["carry_object_max_score"]) >= 0.10 for row in episode_rows),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
