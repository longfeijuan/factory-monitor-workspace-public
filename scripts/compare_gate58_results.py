#!/usr/bin/env python3
"""Compare Gate-58 ledgers against the user's cross-computer tolerance goal."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate58_common import load_json_with_fingerprint  # noqa: E402


DEFAULT_CONFIG = ROOT / "config/gate58-people-crossing-v2.json"


@dataclass(frozen=True)
class Ledger:
    policy: tuple[str, str]
    query: tuple[str, str, str]
    events: list[tuple[str, str]]
    decisions: list[tuple[str, str, str, str]]
    pending: int


def read_ledger(path: Path) -> Ledger:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        rows = list(reader)
    required = {
        "event_time",
        "direction",
        "counted",
        "final_decision",
        "policy_version",
        "config_sha256",
        "query_id",
        "query_start",
        "query_end",
    }
    missing = required - fieldnames
    if missing:
        raise ValueError(f"{path}: 缺少字段{sorted(missing)}")
    if not rows:
        raise ValueError(f"{path}: 空清单无法核验规则与查询指纹")
    policies = {(row["policy_version"], row["config_sha256"]) for row in rows}
    queries = {(row["query_id"], row["query_start"], row["query_end"]) for row in rows}
    if len(policies) != 1:
        raise ValueError(f"{path}: 同一清单混入多个规则版本")
    if len(queries) != 1 or not next(iter(queries))[0]:
        raise ValueError(f"{path}: 同一清单混入多个查询或缺少query_id")
    events = sorted(
        (row["event_time"], row["direction"])
        for row in rows
        if row["counted"] == "是"
    )
    return Ledger(
        next(iter(policies)),
        next(iter(queries)),
        events,
        sorted(
            (
                row["event_time"],
                row["direction"],
                row["final_decision"],
                row["counted"],
            )
            for row in rows
        ),
        sum(row["final_decision"] == "待复核" for row in rows),
    )


def counts(events: list[tuple[str, str]]) -> dict[str, int]:
    enter = sum(direction == "进入" for _, direction in events)
    exit_count = sum(direction == "外出" for _, direction in events)
    return {"enter": enter, "exit": exit_count, "total": enter + exit_count}


def match_with_tolerance(
    left: list[tuple[str, str]],
    right: list[tuple[str, str]],
    tolerance_seconds: float,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], int]:
    left_only: list[tuple[str, str]] = []
    right_only: list[tuple[str, str]] = []
    matched = 0
    for direction in ("进入", "外出"):
        left_times = [datetime.fromisoformat(time) for time, value in left if value == direction]
        right_times = [datetime.fromisoformat(time) for time, value in right if value == direction]
        left_times.sort()
        right_times.sort()
        left_index = right_index = 0
        while left_index < len(left_times) and right_index < len(right_times):
            delta = (left_times[left_index] - right_times[right_index]).total_seconds()
            if abs(delta) <= tolerance_seconds:
                matched += 1
                left_index += 1
                right_index += 1
            elif delta < 0:
                left_only.append((left_times[left_index].isoformat(timespec="seconds"), direction))
                left_index += 1
            else:
                right_only.append((right_times[right_index].isoformat(timespec="seconds"), direction))
                right_index += 1
        left_only.extend(
            (value.isoformat(timespec="seconds"), direction) for value in left_times[left_index:]
        )
        right_only.extend(
            (value.isoformat(timespec="seconds"), direction) for value in right_times[right_index:]
        )
    return sorted(left_only), sorted(right_only), matched


def compare(left: Path, right: Path, config_path: Path = DEFAULT_CONFIG) -> dict:
    config, expected_config_sha = load_json_with_fingerprint(config_path)
    quality = config["quality_gate"]
    left_ledger = read_ledger(left)
    right_ledger = read_ledger(right)
    left_counts = counts(left_ledger.events)
    right_counts = counts(right_ledger.events)
    time_tolerance = float(quality["event_time_tolerance_seconds"])
    left_only, right_only, matched = match_with_tolerance(
        left_ledger.events,
        right_ledger.events,
        time_tolerance,
    )
    policy_match = left_ledger.policy == right_ledger.policy
    query_match = left_ledger.query == right_ledger.query
    expected_policy = str(config["policy_version"])
    expected_rule_match = (
        left_ledger.policy == (expected_policy, expected_config_sha)
        and right_ledger.policy == (expected_policy, expected_config_sha)
    )
    maximum_pending = int(quality["maximum_pending_count_for_official_result"])
    pending_ok = left_ledger.pending <= maximum_pending and right_ledger.pending <= maximum_pending
    differences = {
        key: abs(left_counts[key] - right_counts[key]) for key in ("enter", "exit", "total")
    }
    count_tolerance_ok = (
        differences["enter"] <= int(quality["maximum_cross_computer_enter_difference"])
        and differences["exit"] <= int(quality["maximum_cross_computer_exit_difference"])
        and differences["total"] <= int(quality["maximum_cross_computer_total_difference"])
    )
    accepted = policy_match and query_match and expected_rule_match and pending_ok and count_tolerance_ok
    exact_decisions = Counter(left_ledger.decisions) == Counter(right_ledger.decisions)
    return {
        "accepted_for_user_goal": accepted,
        "identical": policy_match and query_match and exact_decisions,
        "policy_match": policy_match,
        "expected_rule_match": expected_rule_match,
        "query_match": query_match,
        "pending_ok": pending_ok,
        "count_tolerance_ok": count_tolerance_ok,
        "left_counts": left_counts,
        "right_counts": right_counts,
        "absolute_differences": differences,
        "left_pending": left_ledger.pending,
        "right_pending": right_ledger.pending,
        "matched_events_within_seconds": matched,
        "event_time_tolerance_seconds": time_tolerance,
        "left_only": [{"event_time": time, "direction": direction} for time, direction in left_only],
        "right_only": [{"event_time": time, "direction": direction} for time, direction in right_only],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two Gate-58 normalized results")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.left, args.right, args.config)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["accepted_for_user_goal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
