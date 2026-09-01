#!/usr/bin/env python3
"""Build the web catalog from the current sanitized supervisor package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "camera-data" / "current"
DEFAULT_OUTPUT = PROJECT_ROOT / "public" / "data" / "cameras.json"


def load_rows(source: Path) -> list[dict[str, object]]:
    inventory = json.loads((source / "data" / "摄像头脱敏库存.json").read_text(encoding="utf-8"))
    matrix = {
        row["camera_id"]: row
        for row in (
            json.loads(line)
            for line in (source / "data" / "摄像头证据能力矩阵.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    channels = inventory.get("channels", [])
    if not isinstance(channels, list) or len(channels) != inventory.get("expected_channel_count"):
        raise ValueError("脱敏库存数量与expected_channel_count不一致")

    result: list[dict[str, object]] = []
    for channel in channels:
        camera_id = str(channel["camera_id"])
        row = matrix.get(camera_id)
        if not row:
            raise ValueError(f"能力矩阵缺少{camera_id}")
        evidence = row["evidence"]
        names = row["names"]
        locations = row["location_candidates"]
        routes = [
            {
                "id": item["route_id"],
                "title": item["route_title"],
                "sequence": item["sequence"],
                "stage": item["stage_label_zh"],
            }
            for item in row.get("topology_candidates", [])
        ]
        result.append(
            {
                "id": camera_id,
                "name": names.get("normalized_name") or channel.get("display_name_claim") or camera_id,
                "sourceName": names.get("source_name_claim") or channel.get("display_name_claim") or camera_id,
                "recorder": channel["recorder_id"],
                "channel": str(channel["channel_ref"]),
                "onlineClaim": channel.get("online_claim"),
                "evidenceLevel": evidence["level"],
                "evidenceScope": evidence["scope"],
                "frameClockStatus": evidence["frame_clock_status"],
                "timeMappingStatus": evidence["time_mapping_status"],
                "periods": evidence.get("period_ids", []),
                "zones": locations.get("named_zones_zh", []),
                "capabilities": [item["capability_id"] for item in row.get("can_query_with_existing_evidence", [])],
                "routes": routes,
                "unknowns": row.get("unknowns", []),
            }
        )
    if set(matrix) != {str(item["camera_id"]) for item in channels}:
        raise ValueError("库存和能力矩阵的camera_id集合不一致")
    return sorted(result, key=lambda item: str(item["id"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="从当前脱敏包生成工作台摄像头目录")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="只检查生成结果是否已同步")
    args = parser.parse_args()

    rendered = json.dumps(load_rows(args.source), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print("摄像头目录尚未与camera-data/current同步")
            return 1
        print("摄像头目录已同步")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"已生成{len(json.loads(rendered))}路摄像头目录：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
