#!/usr/bin/env python3
"""Apply a complete Gate-58 second-pass decision sheet without ad-hoc scripts."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/gate58-people-crossing-v2.json"
INPUT_FIELDS = (
    "candidate_id",
    "event_time",
    "evidence_start",
    "evidence_end",
    "start_side",
    "boundary_crossed",
    "end_side",
    "occluded",
    "evidence_paths",
    "review_note",
)
DECISION_FIELDS = (
    "candidate_id",
    "start_side",
    "boundary_crossed",
    "end_side",
    "occluded",
    "evidence_paths",
    "review_note",
)
PATCH_FIELDS = DECISION_FIELDS[1:]


def read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: 缺少字段{sorted(missing)}")
        rows = list(reader)
    ids = [row["candidate_id"].strip() for row in rows]
    if any(not value for value in ids):
        raise ValueError(f"{path}: candidate_id不能为空")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: candidate_id不能重复")
    return rows


def apply(
    reviewed_path: Path,
    final_path: Path,
    decisions_path: Path,
    output_path: Path,
    config_path: Path = DEFAULT_CONFIG,
) -> dict:
    reviewed = read_csv(reviewed_path, INPUT_FIELDS)
    final = read_csv(final_path, ("candidate_id", "final_decision"))
    decisions = read_csv(decisions_path, DECISION_FIELDS)
    pending_ids = {
        row["candidate_id"].strip() for row in final if row["final_decision"] == "待复核"
    }
    decision_ids = {row["candidate_id"].strip() for row in decisions}
    if decision_ids != pending_ids:
        missing = sorted(pending_ids - decision_ids)
        extra = sorted(decision_ids - pending_ids)
        raise ValueError(f"二次复核必须完整覆盖当前待复核项；缺少={missing}，多出={extra}")
    reviewed_ids = {row["candidate_id"].strip() for row in reviewed}
    if not pending_ids <= reviewed_ids:
        raise ValueError("最终清单中的待复核ID不在原始审查表中")

    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    pre = float(config["evidence"]["pending_retry_pre_seconds"])
    post = float(config["evidence"]["pending_retry_post_seconds"])
    patches = {row["candidate_id"].strip(): row for row in decisions}
    merged: list[dict[str, str]] = []
    for source in reviewed:
        row = {field: source[field].strip() for field in INPUT_FIELDS}
        candidate_id = row["candidate_id"]
        if candidate_id in patches:
            patch = patches[candidate_id]
            empty = [field for field in PATCH_FIELDS[:-1] if not patch[field].strip()]
            if empty:
                raise ValueError(f"{candidate_id}: 二次复核字段不能为空：{empty}")
            event = datetime.fromisoformat(row["event_time"])
            row["evidence_start"] = (event - timedelta(seconds=pre)).isoformat(timespec="seconds")
            row["evidence_end"] = (event + timedelta(seconds=post)).isoformat(timespec="seconds")
            for field in PATCH_FIELDS:
                row[field] = patch[field].strip()
        merged.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS)
        writer.writeheader()
        writer.writerows(merged)
    return {
        "updated": len(pending_ids),
        "candidate_ids": sorted(pending_ids),
        "output": str(output_path.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Gate-58 pending review decisions")
    parser.add_argument("reviewed_candidates", type=Path)
    parser.add_argument("final_events", type=Path)
    parser.add_argument("pending_decisions", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        result = apply(
            args.reviewed_candidates,
            args.final_events,
            args.pending_decisions,
            args.output,
            args.config,
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
