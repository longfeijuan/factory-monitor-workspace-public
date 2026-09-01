#!/usr/bin/env python3
"""Build a snapshot manifest from completed episode image directories."""

import argparse
import csv
import re
from pathlib import Path


OFFSET_RE = re.compile(r"(?:_|^t)(\d+(?:\.\d+)?)\.jpg$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("episodes_csv")
    parser.add_argument("frames_dir")
    parser.add_argument("output_csv")
    parser.add_argument("episode_ids", nargs="*")
    parser.add_argument("--min-images", type=int, default=1)
    args = parser.parse_args()

    with open(args.episodes_csv, newline="", encoding="utf-8-sig") as handle:
        episodes = {row["episode_id"]: row for row in csv.DictReader(handle)}

    rows = []
    root = Path(args.frames_dir)
    episode_ids = args.episode_ids or sorted(
        path.name for path in root.iterdir() if path.is_dir() and path.name in episodes
    )
    for episode_id in episode_ids:
        episode = episodes[episode_id]
        images = sorted((root / episode_id).glob("*.jpg"))
        if len(images) < args.min_images:
            continue
        for image in images:
            match = OFFSET_RE.search(image.name)
            if not match:
                continue
            rows.append(
                {
                    "episode_id": episode_id,
                    "gate": episode["gate"],
                    "recorder": episode["recorder"],
                    "channel": episode["channel"],
                    "event_local": episode["start_local"],
                    "media_offset_seconds": float(match.group(1)),
                    "image": str(image),
                }
            )

    output = Path(args.output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
