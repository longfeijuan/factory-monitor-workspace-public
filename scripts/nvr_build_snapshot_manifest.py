#!/usr/bin/env python3
"""Build a snapshot manifest from completed episode directories.

This is useful while a long read-only NVR extraction is still running.  Only
episodes with the requested number of decoded frames are included, so a
classifier never sees partially written episodes.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episodes", type=Path)
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-frames", type=int, default=3)
    parser.add_argument("--episode-number-min", type=int, default=0)
    parser.add_argument("--episode-number-max", type=int)
    parser.add_argument("--exclude-manifest", action="append", type=Path, default=[])
    args = parser.parse_args()

    with args.episodes.open(encoding="utf-8", newline="") as handle:
        episodes = list(csv.DictReader(handle))

    excluded: set[str] = set()
    for manifest in args.exclude_manifest:
        with manifest.open(encoding="utf-8", newline="") as handle:
            excluded.update(row["episode_id"] for row in csv.DictReader(handle))

    rows: list[dict[str, str | int | float]] = []
    included_episodes = 0
    for episode in episodes:
        if episode["episode_id"] in excluded:
            continue
        episode_number = int(episode["episode_id"].rsplit("-", 1)[-1])
        if episode_number < args.episode_number_min:
            continue
        if args.episode_number_max is not None and episode_number > args.episode_number_max:
            continue
        paths = sorted((args.frames_dir / episode["episode_id"]).glob("s??_*.jpg"))
        if args.expected_frames > 0 and len(paths) < args.expected_frames:
            continue
        selected_paths = paths if args.expected_frames <= 0 else paths[: args.expected_frames]
        if not selected_paths:
            continue
        included_episodes += 1
        for path in selected_paths:
            match = re.search(r"_(\d+(?:\.\d+)?)\.jpg$", path.name)
            rows.append(
                {
                    "episode_id": episode["episode_id"],
                    "gate": episode["gate"],
                    "recorder": episode["recorder"],
                    "channel": int(episode["channel"]),
                    "event_local": episode["start_local"],
                    "media_offset_seconds": float(match.group(1)) if match else 0.0,
                    "image": str(path),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "episode_id",
        "gate",
        "recorder",
        "channel",
        "event_local",
        "media_offset_seconds",
        "image",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"episodes={included_episodes} frames={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
