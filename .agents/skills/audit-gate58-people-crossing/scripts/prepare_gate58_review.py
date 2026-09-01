#!/usr/bin/env python3
"""Create a versioned, read-only Gate-58 review plan."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(DEFAULT_ROOT / "scripts"))

from gate58_common import load_json_with_fingerprint, query_fingerprint  # noqa: E402


def parse_local(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use YYYY-mm-ddTHH:MM:SS") from exc
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError("use Asia/Shanghai wall time without offset")
    return parsed.replace(microsecond=0)


def shell_join(parts) -> str:
    return shlex.join(str(part) for part in parts)


def git_state(root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "exported-directory", False


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare deterministic Gate-58 review")
    parser.add_argument("--start", required=True, type=parse_local)
    parser.add_argument("--end", required=True, type=parse_local)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--strict-reproducible", action="store_true")
    args = parser.parse_args()
    if args.end <= args.start:
        parser.error("--end must be later than --start")

    root = args.project_root.expanduser().resolve()
    out = args.output_dir.expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("输出目录必须是全新空目录；禁止复用以前任务的候选或结果")
    out.mkdir(parents=True, exist_ok=True)
    config_path = root / "config/gate58-people-crossing-v2.json"
    source_path = root / "camera-data/current/SOURCE.json"
    try:
        config, config_sha = load_json_with_fingerprint(config_path)
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
        parser.error(str(error))

    archive_lag = int(config["reproducibility"]["minimum_archive_lag_seconds"])
    stable_before = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None, microsecond=0) - timedelta(
        seconds=archive_lag
    )
    if args.end > stable_before:
        parser.error(f"结束时间尚未形成稳定回放；最晚只能查到{stable_before.isoformat(timespec='seconds')}")

    commit, dirty = git_state(root)
    if args.strict_reproducible and dirty:
        parser.error("仓库存在未提交改动；跨电脑正式验收必须使用同一干净Git提交")

    camera = config["camera"]
    query_id = query_fingerprint(
        policy_version=str(config["policy_version"]),
        config_sha256=config_sha,
        package_sha256=str(source["zip_sha256"]),
        start_local=args.start.isoformat(timespec="seconds"),
        end_local=args.end.isoformat(timespec="seconds"),
        recorder=str(camera["recorder"]),
        channel=int(camera["channel"]),
        track=int(camera["track"]),
        code_version=commit,
    )

    motion_csv = out / "motion_events.csv"
    episodes_csv = out / "motion_episodes.csv"
    frames_dir = out / "continuous_review_frames"
    reviewed_csv = out / "reviewed_candidates.csv"
    final_csv = out / "final_events.csv"
    summary_json = out / "summary.json"
    pending_episodes_csv = out / "pending_episodes.csv"
    pending_frames_dir = out / "pending_review_frames"
    pending_decisions_csv = out / "pending_decisions.csv"
    reviewed_round2_csv = out / "reviewed_candidates_round2.csv"
    with reviewed_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "candidate_id", "event_time", "evidence_start", "evidence_end",
            "start_side", "boundary_crossed", "end_side", "occluded",
            "evidence_paths", "review_note",
        ])

    evidence = config["evidence"]
    pre_seconds = str(evidence["minimum_pre_seconds"])
    post_seconds = str(evidence["minimum_post_seconds"])
    retry_pre_seconds = str(evidence["pending_retry_pre_seconds"])
    retry_post_seconds = str(evidence["pending_retry_post_seconds"])
    sample_step = str(evidence["maximum_sample_interval_seconds"])

    commands = [
        shell_join([
            "python3", root / "scripts/nvr_log_motion_events.py",
            "--start", args.start.isoformat(timespec="seconds"),
            "--end", args.end.isoformat(timespec="seconds"),
            "--output", motion_csv,
            "--window-minutes", "30", "--workers", "4",
            "--target", "nvr-main-02:1:大井街58号大门",
        ]),
        shell_join([
            "python3", root / "scripts/nvr_merge_motion_events.py",
            motion_csv, episodes_csv, "--gap-seconds", "20",
        ]),
        shell_join([
            "python3", root / "scripts/nvr_event_snapshots.py",
            episodes_csv, frames_dir,
            "--workers", "1", "--scale", "4",
            "--pre-seconds", pre_seconds, "--post-seconds", post_seconds,
            "--sample-start", "0", "--sample-step", sample_step,
            "--max-width", "960", "--per-recorder-sessions", "1",
            "--nvr-wall-clock",
        ]),
        shell_join([
            "python3", root / "scripts/gate58_review_contract.py",
            reviewed_csv, final_csv, "--summary", summary_json,
            "--query-start", args.start.isoformat(timespec="seconds"),
            "--query-end", args.end.isoformat(timespec="seconds"),
            "--query-id", query_id,
        ]),
    ]
    pending_review_commands = [
        shell_join([
            "python3", root / "scripts/gate58_pending_manifest.py",
            final_csv, pending_episodes_csv,
            "--recorder", camera["recorder"], "--channel", camera["channel"],
            "--decisions-template", pending_decisions_csv,
        ]),
        shell_join([
            "python3", root / "scripts/nvr_event_snapshots.py",
            pending_episodes_csv, pending_frames_dir,
            "--workers", "1", "--scale", "1",
            "--pre-seconds", retry_pre_seconds, "--post-seconds", retry_post_seconds,
            "--sample-start", "0", "--sample-step", sample_step,
            "--max-width", "960", "--per-recorder-sessions", "1",
            "--nvr-wall-clock",
        ]),
        shell_join([
            "python3", root / "scripts/gate58_apply_pending_reviews.py",
            reviewed_csv, final_csv, pending_decisions_csv, reviewed_round2_csv,
        ]),
        shell_join([
            "python3", root / "scripts/gate58_review_contract.py",
            reviewed_round2_csv, final_csv, "--summary", summary_json,
            "--query-start", args.start.isoformat(timespec="seconds"),
            "--query-end", args.end.isoformat(timespec="seconds"),
            "--query-id", query_id,
        ]),
    ]
    plan = {
        "policy_version": config["policy_version"],
        "config_sha256": config_sha,
        "config_fingerprint_mode": config["reproducibility"]["config_fingerprint"],
        "query_id": query_id,
        "camera_package": source,
        "git_commit": commit,
        "git_dirty": dirty,
        "timezone": "Asia/Shanghai",
        "start_local": args.start.isoformat(timespec="seconds"),
        "end_local": args.end.isoformat(timespec="seconds"),
        "camera": camera,
        "evidence": config["evidence"],
        "quality_gate": config["quality_gate"],
        "files": {
            "motion_events": str(motion_csv),
            "motion_episodes": str(episodes_csv),
            "continuous_review_frames": str(frames_dir),
            "reviewed_candidates": str(reviewed_csv),
            "final_events": str(final_csv),
            "summary": str(summary_json),
            "pending_episodes": str(pending_episodes_csv),
            "pending_review_frames": str(pending_frames_dir),
            "pending_decisions": str(pending_decisions_csv),
            "reviewed_candidates_round2": str(reviewed_round2_csv),
        },
        "commands": commands,
        "pending_review_commands": pending_review_commands,
        "acceptance": "同一输入复跑必须完全一致；跨电脑进入、外出、合计各自差值不超过2，且每边待复核不超过2。",
        "do_not_return_intermediate_result": True,
    }
    plan_path = out / "review_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"review_plan": str(plan_path), "commands": commands}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
