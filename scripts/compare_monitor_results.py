#!/usr/bin/env python3
"""Compare two project-standard monitoring result envelopes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(left_path: Path, right_path: Path) -> dict:
    left = json.loads(left_path.read_text(encoding="utf-8-sig"))
    right = json.loads(right_path.read_text(encoding="utf-8-sig"))
    query_match = left["query_id"] == right["query_id"]
    quality_gate_pass = left["quality_gate"] == right["quality_gate"] == "pass"
    left_metrics = left["metrics"]
    right_metrics = right["metrics"]
    metric_set_match = set(left_metrics) == set(right_metrics)
    differences = {}
    all_within_tolerance = metric_set_match
    for name in sorted(set(left_metrics) | set(right_metrics)):
        if name not in left_metrics or name not in right_metrics:
            differences[name] = {"status": "missing"}
            all_within_tolerance = False
            continue
        lvalue = left_metrics[name]
        rvalue = right_metrics[name]
        same_unit = lvalue["unit"] == rvalue["unit"]
        same_tolerance = float(lvalue["tolerance"]) == float(rvalue["tolerance"])
        difference = abs(float(lvalue["value"]) - float(rvalue["value"]))
        tolerance = min(float(lvalue["tolerance"]), float(rvalue["tolerance"]))
        within = same_unit and same_tolerance and difference <= tolerance
        all_within_tolerance = all_within_tolerance and within
        differences[name] = {
            "left": lvalue["value"],
            "right": rvalue["value"],
            "unit": lvalue["unit"] if same_unit else "mismatch",
            "absolute_difference": difference,
            "tolerance": tolerance,
            "within_tolerance": within,
        }
    accepted = query_match and quality_gate_pass and all_within_tolerance
    return {
        "accepted_for_user_goal": accepted,
        "identical": query_match
        and left["quality_gate"] == right["quality_gate"]
        and left["normalized_result_sha256"] == right["normalized_result_sha256"],
        "query_match": query_match,
        "quality_gate_pass": quality_gate_pass,
        "metric_set_match": metric_set_match,
        "all_metrics_within_tolerance": all_within_tolerance,
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare monitoring results")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.left, args.right)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["accepted_for_user_goal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
