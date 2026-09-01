#!/usr/bin/env python3
"""Validate Gate-58 review rows and emit a deterministic final ledger.

The script deliberately does not infer direction from a person's presence in
the doorway.  A counted row must contain the complete side-to-side sequence,
at least ten seconds of evidence before and after the event, and three saved
evidence nodes.  Invalid or ambiguous inputs fail closed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate58_common import load_json_with_fingerprint  # noqa: E402


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
OUTPUT_FIELDS = (
    "candidate_id",
    "event_time",
    "direction",
    "final_decision",
    "counted",
    "evidence_start",
    "evidence_end",
    "evidence_paths",
    "review_note",
    "policy_version",
    "config_sha256",
    "query_id",
    "query_start",
    "query_end",
)
SIDES = {"inside", "outside", "boundary", "unknown"}
TRI = {"yes", "no", "unknown"}
NO_CANDIDATE_ID = "__NO_CANDIDATES__"


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    direction: str
    final_decision: str
    counted: str
    note: str


def parse_time(value: str, field: str, candidate_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{candidate_id}: {field}不是ISO本地时间：{value}") from exc
    if parsed.tzinfo is not None:
        raise ContractError(f"{candidate_id}: {field}必须是Asia/Shanghai无偏移墙钟时间")
    return parsed


def normalized_bool(value: str, field: str, candidate_id: str) -> str:
    normalized = value.strip().lower()
    aliases = {"是": "yes", "否": "no", "待复核": "unknown"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in TRI:
        raise ContractError(f"{candidate_id}: {field}必须是yes/no/unknown")
    return normalized


def config_fingerprint(config_path: Path) -> tuple[dict, str]:
    return load_json_with_fingerprint(config_path)


def evidence_nodes(value: str) -> list[Path]:
    return [Path(item.strip()).expanduser() for item in value.split("|") if item.strip()]


def decide(row: dict[str, str], config: dict, check_paths: bool) -> Decision:
    candidate_id = row["candidate_id"].strip()
    if not candidate_id:
        raise ContractError("candidate_id不能为空")
    event = parse_time(row["event_time"], "event_time", candidate_id)
    start = parse_time(row["evidence_start"], "evidence_start", candidate_id)
    end = parse_time(row["evidence_end"], "evidence_end", candidate_id)
    minimum_pre = float(config["evidence"]["minimum_pre_seconds"])
    minimum_post = float(config["evidence"]["minimum_post_seconds"])
    if (event - start).total_seconds() < minimum_pre:
        raise ContractError(f"{candidate_id}: 事件前证据不足{minimum_pre:g}秒")
    if (end - event).total_seconds() < minimum_post:
        raise ContractError(f"{candidate_id}: 事件后证据不足{minimum_post:g}秒")

    start_side = row["start_side"].strip().lower()
    end_side = row["end_side"].strip().lower()
    if start_side not in SIDES or end_side not in SIDES:
        raise ContractError(f"{candidate_id}: start_side/end_side必须属于{sorted(SIDES)}")
    crossed = normalized_bool(row["boundary_crossed"], "boundary_crossed", candidate_id)
    occluded = normalized_bool(row["occluded"], "occluded", candidate_id)
    paths = evidence_nodes(row["evidence_paths"])
    minimum_review_nodes = int(config["evidence"].get("minimum_review_nodes", 1))
    if len(paths) < minimum_review_nodes:
        raise ContractError(
            f"{candidate_id}: 每条判定至少需要{minimum_review_nodes}个证据节点，待复核也不得留空"
        )
    if check_paths:
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise ContractError(f"{candidate_id}: 证据文件不存在：{missing[0]}")

    ambiguous = (
        occluded != "no"
        or crossed == "unknown"
        or start_side in {"unknown", "boundary"}
        or end_side in {"unknown", "boundary"}
    )
    if ambiguous:
        return Decision("", "待复核", "否", "遮挡、端点或跨门状态不完整，按统一口径不计数。")

    if start_side == end_side and crossed == "yes":
        raise ContractError(
            f"{candidate_id}: 起终点同侧但标记已跨门；完整折返必须拆成每次越界一行"
        )
    if start_side != end_side and crossed == "no":
        raise ContractError(f"{candidate_id}: 起终点位于门界两侧但标记未跨门；请复核连续证据")

    if crossed == "yes" and start_side == "outside" and end_side == "inside":
        required = int(config["evidence"].get("required_counted_nodes", 3))
        if len(paths) < required:
            raise ContractError(f"{candidate_id}: 明确进入至少需要{required}个证据节点")
        decision = Decision("进入", "明确进入", "是", "完整确认outside→boundary→inside。")
    elif crossed == "yes" and start_side == "inside" and end_side == "outside":
        required = int(config["evidence"].get("required_counted_nodes", 3))
        if len(paths) < required:
            raise ContractError(f"{candidate_id}: 明确外出至少需要{required}个证据节点")
        decision = Decision("外出", "明确外出", "是", "完整确认inside→boundary→outside。")
    else:
        decision = Decision("", "明确不构成进出", "否", "未形成完整的两侧越界链路。")

    return decision


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in INPUT_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ContractError("输入CSV缺少字段：" + ", ".join(missing))
        rows = list(reader)
    ids = [row["candidate_id"].strip() for row in rows]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ContractError("candidate_id重复：" + ", ".join(duplicates))
    return rows


def normalized_result_hash(rows: list[dict[str, str]]) -> str:
    payload = [
        {"event_time": row["event_time"], "direction": row["direction"]}
        for row in rows
        if row["counted"] == "是"
    ]
    payload.sort(key=lambda row: (row["event_time"], row["direction"]))
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def finalize(
    input_path: Path,
    output_path: Path,
    summary_path: Path,
    config_path: Path,
    check_paths: bool = True,
    query_start: datetime | None = None,
    query_end: datetime | None = None,
    query_id: str = "",
) -> dict:
    config, config_sha = config_fingerprint(config_path)
    policy_version = str(config["policy_version"])
    if policy_version == "gate58-people-crossing-v2":
        if query_start is None or query_end is None or not query_id.strip():
            raise ContractError("v2正式结果必须携带query_start、query_end和query_id")
        if query_end <= query_start:
            raise ContractError("query_end必须晚于query_start")
    source_rows = read_rows(input_path)
    final_rows: list[dict[str, str]] = []
    for row in source_rows:
        event = parse_time(row["event_time"], "event_time", row["candidate_id"].strip())
        if query_start is not None and query_end is not None and not (query_start <= event < query_end):
            raise ContractError(
                f"{row['candidate_id']}: event_time超出查询时段{query_start.isoformat()}—{query_end.isoformat()}"
            )
        decision = decide(row, config, check_paths)
        combined_note = decision.note
        if row["review_note"].strip():
            combined_note += " " + row["review_note"].strip()
        final_rows.append(
            {
                "candidate_id": row["candidate_id"].strip(),
                "event_time": row["event_time"].strip(),
                "direction": decision.direction,
                "final_decision": decision.final_decision,
                "counted": decision.counted,
                "evidence_start": row["evidence_start"].strip(),
                "evidence_end": row["evidence_end"].strip(),
                "evidence_paths": row["evidence_paths"].strip(),
                "review_note": combined_note,
                "policy_version": policy_version,
                "config_sha256": config_sha,
                "query_id": query_id.strip(),
                "query_start": query_start.isoformat(timespec="seconds") if query_start else "",
                "query_end": query_end.isoformat(timespec="seconds") if query_end else "",
            }
        )
    final_rows.sort(key=lambda row: (row["event_time"], row["candidate_id"]))
    output_rows = final_rows
    if not output_rows:
        output_rows = [
            {
                "candidate_id": NO_CANDIDATE_ID,
                "event_time": "",
                "direction": "",
                "final_decision": "无候选",
                "counted": "否",
                "evidence_start": "",
                "evidence_end": "",
                "evidence_paths": "",
                "review_note": "查询时段内没有候选；本行仅保存可核验的规则与查询指纹。",
                "policy_version": policy_version,
                "config_sha256": config_sha,
                "query_id": query_id.strip(),
                "query_start": query_start.isoformat(timespec="seconds") if query_start else "",
                "query_end": query_end.isoformat(timespec="seconds") if query_end else "",
            }
        ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    counted = [row for row in final_rows if row["counted"] == "是"]
    pending = sum(row["final_decision"] == "待复核" for row in final_rows)
    excluded = sum(row["final_decision"] == "明确不构成进出" for row in final_rows)
    maximum_pending = int(
        config.get("quality_gate", {}).get("maximum_pending_count_for_official_result", 0)
    )
    quality_gate = "pass" if pending <= maximum_pending else "needs_review"
    summary = {
        "policy_version": policy_version,
        "config_sha256": config_sha,
        "query_id": query_id.strip(),
        "query_start": query_start.isoformat(timespec="seconds") if query_start else "",
        "query_end": query_end.isoformat(timespec="seconds") if query_end else "",
        "rows": len(final_rows),
        "enter": sum(row["direction"] == "进入" for row in counted),
        "exit": sum(row["direction"] == "外出" for row in counted),
        "total": len(counted),
        "pending": pending,
        "excluded": excluded,
        "minimum_possible_total": len(counted),
        "maximum_possible_total": len(counted) + pending,
        "maximum_pending_for_official_result": maximum_pending,
        "quality_gate": quality_gate,
        "official_result": quality_gate == "pass",
        "normalized_result_sha256": normalized_result_hash(final_rows),
        "output_csv": str(output_path.resolve()),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate-58 deterministic review contract")
    parser.add_argument("input", type=Path, help="reviewed candidate CSV")
    parser.add_argument("output", type=Path, help="final ledger CSV")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--query-start", required=True, type=lambda value: parse_time(value, "query_start", "query"))
    parser.add_argument("--query-end", required=True, type=lambda value: parse_time(value, "query_end", "query"))
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--skip-path-check", action="store_true", help="tests only")
    args = parser.parse_args()
    try:
        summary = finalize(
            args.input,
            args.output,
            args.summary,
            args.config,
            check_paths=not args.skip_path_check,
            query_start=args.query_start,
            query_end=args.query_end,
            query_id=args.query_id,
        )
    except (OSError, KeyError, json.JSONDecodeError, ContractError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
