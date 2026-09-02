#!/usr/bin/env python3
"""Run the versioned first-floor CNC runtime workflow end to end."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CONFIG_PATH = ROOT / "config" / "cnc-floor1-runtime-v3.json"


def parse_local_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError("时间必须是Asia/Shanghai无偏移墙钟时间")
    return parsed.replace(microsecond=0)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(" ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=check, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="一楼电脑锣六台机固定口径开机率")
    parser.add_argument("--start", required=True, type=parse_local_time)
    parser.add_argument("--end", required=True, type=parse_local_time)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--strict-reproducible", action="store_true")
    args = parser.parse_args()

    root = args.project_root.expanduser().resolve()
    config_path = root / "config" / "cnc-floor1-runtime-v3.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if args.end <= args.start:
        parser.error("结束时间必须晚于开始时间")
    valid_from = datetime.fromisoformat(config["valid_from_local"])
    if args.start < valid_from:
        parser.error(
            f"v3双通道灯位只适用于{valid_from.isoformat(timespec='seconds')}之后的录像；"
            "历史画面必须另用经审核的旧版本标定"
        )

    output = args.output_dir.expanduser().resolve()
    sampling = config["sampling"]
    parameters = {
        "sources": config["sources"],
        "machine_source_map": config["machine_source_map"],
        "sampling": sampling,
        "scheduled_work_periods": config["scheduled_work_periods"],
    }
    context_command = [
        sys.executable,
        str(root / "scripts" / "monitor_query_context.py"),
        "--task-type",
        "floor1-cnc-six-machine-runtime",
        "--task-policy-version",
        config["policy_version"],
        "--start",
        args.start.isoformat(timespec="seconds"),
        "--end",
        args.end.isoformat(timespec="seconds"),
        "--parameters-json",
        json.dumps(parameters, ensure_ascii=False, sort_keys=True),
        "--task-config",
        str(config_path),
        "--output-dir",
        str(output),
        "--project-root",
        str(root),
    ]
    if args.strict_reproducible:
        context_command.append("--strict")
    run(context_command)

    source_episode_paths: list[tuple[str, Path]] = []
    for source_id, source in config["sources"].items():
        camera = source["camera"]
        source_episodes = output / f"episodes-{source_id}.csv"
        run(
            [
                sys.executable,
                str(root / "scripts" / "generate_cnc_indicator_episodes.py"),
                args.start.isoformat(timespec="seconds"),
                args.end.isoformat(timespec="seconds"),
                str(sampling["step_minutes"]),
                str(source_episodes),
                "--channel",
                str(camera["channel"]),
                "--gate",
                f"一楼电脑锣六台机-{source_id}",
            ]
        )
        source_episode_paths.append((source_id, source_episodes))

    episodes = output / "episodes.csv"
    combined_rows: list[dict[str, str | int]] = []
    for source_id, source_path in source_episode_paths:
        source = config["sources"][source_id]
        camera = source["camera"]
        with source_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                row["episode_id"] = f"{source_id}-{row['episode_id']}"
                row["source_id"] = source_id
                row["recorder"] = camera["recorder"]
                row["channel"] = str(camera["channel"])
                row["track"] = str(camera["track"])
                row["source_label"] = camera["source_label"]
                combined_rows.append(row)
    combined_rows.sort(key=lambda row: (str(row["start_local"]), str(row["source_id"])))
    with episodes.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "episode_id",
            "source_id",
            "source_label",
            "gate",
            "recorder",
            "channel",
            "track",
            "start_local",
            "end_local",
            "trigger_count",
            "span_seconds",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(combined_rows)

    analysis = output / "analysis"

    def analyze(workers: int) -> subprocess.CompletedProcess[str]:
        return run(
            [
                sys.executable,
                str(root / "scripts" / "analyze_cnc_six_green_blink.py"),
                str(episodes),
                str(analysis),
                "--config",
                str(config_path),
                "--seconds",
                str(sampling["window_seconds"]),
                "--step",
                str(sampling["frame_step_seconds"]),
                "--minimum-span",
                str(sampling["minimum_span_seconds"]),
                "--minimum-green-frames",
                str(sampling["minimum_green_frames"]),
                "--strong-single-frame-green-pixels",
                str(sampling["strong_single_frame_green_pixels"]),
                "--ambiguous-review-seconds",
                str(sampling["ambiguous_review_window_seconds"]),
                "--ambiguous-review-minimum-span",
                str(sampling["ambiguous_review_minimum_span_seconds"]),
                "--threshold",
                str(sampling["green_pixel_threshold"]),
                "--dominance-threshold",
                str(sampling["dominance_threshold"]),
                "--event-shift-seconds",
                str(sampling["event_shift_seconds"]),
                "--workers",
                str(workers),
                "--resume",
            ],
            check=False,
        )

    first = analyze(int(sampling["parallel_workers"]))
    if first.returncode:
        print("并行首轮存在失败或质量闸门未通过；按固定规则单线程重试未完成窗口。", flush=True)
        analyze(int(sampling["retry_workers"]))

    metrics = analysis / "metrics-input.json"
    envelope = output / "result-envelope.json"
    run(
        [
            sys.executable,
            str(root / "scripts" / "monitor_result_contract.py"),
            str(output / "run-context.json"),
            str(metrics),
            str(envelope),
        ]
    )
    result = json.loads(envelope.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "query_id": result["query_id"],
                "quality_gate": result["quality_gate"],
                "official_result": result["official_result"],
                "normalized_result_sha256": result["normalized_result_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["official_result"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
