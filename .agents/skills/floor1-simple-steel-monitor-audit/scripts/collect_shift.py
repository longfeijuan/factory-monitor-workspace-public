#!/usr/bin/env python3
"""Collect and preliminarily analyze one simple-steel day or night shift."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path


DEFAULT_PROJECT = Path(__file__).resolve().parents[4]


def project_root(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["SIMPLE_STEEL_PROJECT"]) if os.environ.get("SIMPLE_STEEL_PROJECT") else None,
        Path.cwd() / "lost-item-investigator",
        DEFAULT_PROJECT,
    ]
    for candidate in candidates:
        if candidate and (candidate / "scripts/nvr_event_snapshots.py").exists():
            return candidate.resolve()
    raise SystemExit("lost-item-investigator not found; set SIMPLE_STEEL_PROJECT")


def run(command: list[str], check: bool = True) -> int:
    print("RUN", " ".join(command), flush=True)
    result = subprocess.run(command, check=False)
    if check and result.returncode:
        raise SystemExit(result.returncode)
    return result.returncode


def count_manifest(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def semantic_csv_sha256(path: Path | None) -> str:
    if path is None:
        return "none"
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="shift start date, YYYY-MM-DD")
    parser.add_argument("--shift", choices=("day", "night"), required=True)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--strict-reproducible", action="store_true")
    args = parser.parse_args()

    root = project_root(args.project_root)
    shift_date = date.fromisoformat(args.date)
    if args.shift == "day":
        start = datetime.combine(shift_date, time(8))
        end = datetime.combine(shift_date, time(20))
    else:
        start = datetime.combine(shift_date, time(20))
        end = datetime.combine(shift_date + timedelta(days=1), time(8))

    now = datetime.now()
    effective_end = min(end, now.replace(second=0, microsecond=0))
    effective_end -= timedelta(minutes=effective_end.minute % 5)
    if effective_end <= start:
        raise SystemExit("requested shift has not started")
    suffix = "day" if args.shift == "day" else "night"
    run_stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = args.output or (
        root / "audit-output" / f"{shift_date.isoformat()}-simple-steel-{suffix}-run-{run_stamp}"
    )
    py = sys.executable

    context_command = [
        py,
        str(root / "scripts/monitor_query_context.py"),
        "--task-type",
        "floor1-simple-steel-runtime",
        "--task-policy-version",
        "floor1-simple-steel-runtime-v2",
        "--start",
        start.isoformat(timespec="seconds"),
        "--end",
        effective_end.isoformat(timespec="seconds"),
        "--parameters-json",
        json.dumps(
            {
                "shift": args.shift,
                "channels": [59, 31],
                "sample_interval_minutes": 5,
                "manual_overrides_sha256": semantic_csv_sha256(args.overrides),
            },
            separators=(",", ":"),
        ),
        "--output-dir",
        str(output),
        "--project-root",
        str(root),
    ]
    if args.strict_reproducible:
        context_command.append("--strict")
    run(context_command)

    for channel in (59, 31):
        episodes = output / f"ch{channel}-episodes.csv"
        frames = output / f"ch{channel}-frames"
        gate = f"一楼简易钢件{channel}号视角"
        run(
            [
                py,
                str(root / "scripts/generate_cnc_indicator_episodes.py"),
                start.isoformat(timespec="seconds"),
                effective_end.isoformat(timespec="seconds"),
                "5",
                str(episodes),
                "--channel",
                str(channel),
                "--gate",
                gate,
            ]
        )
        common = [
            py,
            str(root / "scripts/nvr_event_snapshots.py"),
            str(episodes),
            str(frames),
            "--scale",
            "8",
            "--pre-seconds",
            "0",
            "--post-seconds",
            "1",
            "--sample-start",
            "0",
            "--sample-step",
            "10",
            "--max-width",
            "2560",
            "--retries",
            "2",
            "--resume",
            "--nvr-wall-clock",
        ]
        run(common + ["--workers", "2", "--per-recorder-sessions", "2"], check=False)
        manifest = frames / "snapshots.csv"
        expected = count_manifest(episodes)
        if count_manifest(manifest) < expected * 0.95:
            run(common + ["--workers", "1", "--per-recorder-sessions", "1"], check=False)
        # Rebuild from complete frame directories even if the extractor exited non-zero.
        run(
            [
                py,
                str(root / "scripts/nvr_build_snapshot_manifest.py"),
                str(episodes),
                str(frames),
                str(manifest),
                "--expected-frames",
                "1",
            ]
        )

    analyzer = Path(__file__).resolve().parent / "analyze_shift.py"
    command = [
        py,
        str(analyzer),
        "--ch59-manifest",
        str(output / "ch59-frames/snapshots.csv"),
        "--ch31-manifest",
        str(output / "ch31-frames/snapshots.csv"),
        "--output",
        str(output),
        "--shift",
        args.shift,
        "--start",
        start.isoformat(timespec="seconds"),
        "--end",
        effective_end.isoformat(timespec="seconds"),
    ]
    if args.overrides:
        command += ["--overrides", str(args.overrides)]
    run(command)
    run(
        [
            py,
            str(root / "scripts/monitor_result_contract.py"),
            str(output / "run-context.json"),
            str(output / "metrics-input.json"),
            str(output / "result-envelope.json"),
        ]
    )
    print(f"output={output} requested_end={end.isoformat()} effective_end={effective_end.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
