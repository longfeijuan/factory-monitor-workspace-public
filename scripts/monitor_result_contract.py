#!/usr/bin/env python3
"""Seal monitoring metrics into a deterministic, comparable result envelope."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate58_common import canonical_json_bytes  # noqa: E402


UNITS = {"count", "percentage_point", "minutes"}


def finalize(context_path: Path, metrics_path: Path, output_path: Path) -> dict:
    context = json.loads(context_path.read_text(encoding="utf-8-sig"))
    source = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
    quality_gate = source.get("quality_gate")
    if quality_gate not in {"pass", "needs_review"}:
        raise ValueError("quality_gate必须是pass或needs_review")
    metrics = source.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics必须是非空JSON对象")
    normalized = {}
    for name in sorted(metrics):
        item = metrics[name]
        if not isinstance(item, dict):
            raise ValueError(f"{name}: 指标必须是JSON对象")
        unit = item.get("unit")
        if unit not in UNITS:
            raise ValueError(f"{name}: unit必须属于{sorted(UNITS)}")
        value = item.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name}: value必须是有限数值")
        tolerance = item.get("tolerance", context["default_tolerances"][unit])
        if not isinstance(tolerance, (int, float)) or tolerance < 0:
            raise ValueError(f"{name}: tolerance必须是非负数")
        normalized[name] = {"value": value, "unit": unit, "tolerance": tolerance}
    result_hash = hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()
    result = {
        "schema_version": 1,
        "query_id": context["query_id"],
        "task_type": context["task_type"],
        "task_policy_version": context["task_policy_version"],
        "git_commit": context["git_commit"],
        "source_package_sha256": context["source_package_sha256"],
        "start_local": context["start_local"],
        "end_local": context["end_local"],
        "quality_gate": quality_gate,
        "official_result": quality_gate == "pass",
        "metrics": normalized,
        "normalized_result_sha256": result_hash,
        "notes": source.get("notes", []),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize monitoring result metrics")
    parser.add_argument("run_context", type=Path)
    parser.add_argument("metrics", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = finalize(args.run_context, args.metrics, args.output)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
