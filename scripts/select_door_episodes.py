#!/usr/bin/env python3
"""Select named door episodes from a CSV while preserving its schema."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("episode_ids", nargs="+")
    args = parser.parse_args()

    wanted = set(args.episode_ids)
    with args.source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [row for row in reader if row["episode_id"] in wanted]
        fields = reader.fieldnames
    assert fields
    rows.sort(key=lambda row: row["start_local"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    missing = sorted(wanted - {row["episode_id"] for row in rows})
    print(f"selected={len(rows)} missing={','.join(missing) if missing else '-'} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
