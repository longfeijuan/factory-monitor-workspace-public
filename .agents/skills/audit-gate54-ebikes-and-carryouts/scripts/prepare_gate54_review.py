#!/usr/bin/env python3
"""Prepare a read-only, token-efficient review plan for Gate 54 footage."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
from datetime import datetime, timedelta
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[4]
CAMERAS = (
    {"label": "54号大门主画面", "recorder": "nvr-main-01", "channel": 41},
    {"label": "54号大门辅助画面", "recorder": "nvr-main-01", "channel": 64},
)
FIELDS = (
    "episode_id",
    "gate",
    "recorder",
    "channel",
    "start_local",
    "end_local",
    "trigger_count",
    "span_seconds",
)


def parse_local(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "use local time in YYYY-mm-ddTHH:MM:SS format"
        ) from exc
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError(
            "provide Asia/Shanghai wall time without a UTC offset"
        )
    return parsed.replace(microsecond=0)


def shell_join(parts: list[str | Path]) -> str:
    return shlex.join(str(part) for part in parts)


def choose_interval(start: datetime, end: datetime, requested: int | None) -> int:
    if requested is not None:
        if requested <= 0:
            raise ValueError("interval minutes must be positive")
        return requested
    return 15 if end - start <= timedelta(hours=4) else 30


def sample_times(start: datetime, end: datetime, interval_minutes: int) -> list[datetime]:
    result = [start]
    cursor = start + timedelta(minutes=interval_minutes)
    while cursor < end:
        result.append(cursor)
        cursor += timedelta(minutes=interval_minutes)
    if result[-1] != end:
        result.append(end)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Gate 54 parking samples and motion-review commands."
    )
    parser.add_argument("--start", required=True, type=parse_local)
    parser.add_argument("--end", required=True, type=parse_local)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--interval-minutes", type=int)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    args = parser.parse_args()

    if args.end <= args.start:
        parser.error("--end must be later than --start")
    try:
        interval = choose_interval(args.start, args.end, args.interval_minutes)
    except ValueError as exc:
        parser.error(str(exc))

    output_dir = args.output_dir.expanduser().resolve()
    project_root = args.project_root.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    parking_csv = output_dir / "parking_samples.csv"
    rows: list[dict[str, str | int]] = []
    for when in sample_times(args.start, args.end, interval):
        stamp = when.strftime("%Y%m%dT%H%M%S")
        local_time = when.isoformat(timespec="seconds")
        for camera in CAMERAS:
            rows.append(
                {
                    "episode_id": f"gate54-ch{camera['channel']}-{stamp}",
                    "gate": camera["label"],
                    "recorder": camera["recorder"],
                    "channel": camera["channel"],
                    "start_local": local_time,
                    "end_local": local_time,
                    "trigger_count": 1,
                    "span_seconds": 0,
                }
            )
    with parking_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    motion_csv = output_dir / "motion_events.csv"
    episodes_csv = output_dir / "motion_episodes.csv"
    parking_frames = output_dir / "parking_frames"
    motion_frames = output_dir / "motion_preview_frames"

    log_script = project_root / "scripts/nvr_log_motion_events.py"
    merge_script = project_root / "scripts/nvr_merge_motion_events.py"
    snapshots_script = project_root / "scripts/nvr_event_snapshots.py"

    motion_command = shell_join(
        [
            "python3",
            log_script,
            "--start",
            args.start.isoformat(timespec="seconds"),
            "--end",
            args.end.isoformat(timespec="seconds"),
            "--output",
            motion_csv,
            "--window-minutes",
            "30",
            "--workers",
            "4",
            "--target",
            "nvr-main-01:41:54号大门主画面",
            "--target",
            "nvr-main-01:64:54号大门辅助画面",
        ]
    )
    merge_command = shell_join(
        ["python3", merge_script, motion_csv, episodes_csv, "--gap-seconds", "60"]
    )
    parking_command = shell_join(
        [
            "python3",
            snapshots_script,
            parking_csv,
            parking_frames,
            "--workers",
            "1",
            "--scale",
            "8",
            "--pre-seconds",
            "-4",
            "--post-seconds",
            "5",
            "--sample-start",
            "0",
            "--sample-step",
            "10",
            "--max-width",
            "1280",
            "--per-recorder-sessions",
            "1",
            "--nvr-wall-clock",
        ]
    )
    motion_preview_command = shell_join(
        [
            "python3",
            snapshots_script,
            episodes_csv,
            motion_frames,
            "--workers",
            "1",
            "--scale",
            "8",
            "--pre-seconds",
            "8",
            "--post-seconds",
            "12",
            "--sample-start",
            "0",
            "--sample-step",
            "3",
            "--max-width",
            "1280",
            "--per-recorder-sessions",
            "1",
            "--nvr-wall-clock",
        ]
    )

    plan = {
        "gate": "54号大门",
        "timezone": "Asia/Shanghai",
        "start_local": args.start.isoformat(timespec="seconds"),
        "end_local": args.end.isoformat(timespec="seconds"),
        "parking_interval_minutes": interval,
        "parking_sample_times": len(rows) // len(CAMERAS),
        "cameras": list(CAMERAS),
        "files": {
            "parking_samples": str(parking_csv),
            "motion_events": str(motion_csv),
            "motion_episodes": str(episodes_csv),
            "parking_frames": str(parking_frames),
            "motion_preview_frames": str(motion_frames),
        },
        "commands": [
            {"step": "extract parking samples", "command": parking_command},
            {"step": "query motion events", "command": motion_command},
            {"step": "merge motion events", "command": merge_command},
            {"step": "extract motion previews", "command": motion_preview_command},
        ],
        "reminder": (
            "Only count continuous inside-to-outside carry-outs; exclude phones, "
            "umbrellas, outside passersby, inbound people, and unclear direction."
        ),
    }
    plan_path = output_dir / "review_plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"parking samples: {len(rows)} frames across {len(rows) // 2} times")
    print(f"review plan: {plan_path}")
    for item in plan["commands"]:
        print(f"[{item['step']}] {item['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
