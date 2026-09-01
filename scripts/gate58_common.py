#!/usr/bin/env python3
"""Shared deterministic helpers for Gate-58 audits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON independently of OS line endings and indentation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_json_with_fingerprint(path: Path) -> tuple[dict[str, Any], str]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}不是JSON对象")
    return value, hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def query_fingerprint(
    *,
    policy_version: str,
    config_sha256: str,
    package_sha256: str,
    start_local: str,
    end_local: str,
    recorder: str,
    channel: int,
    track: int,
    code_version: str,
) -> str:
    payload = {
        "policy_version": policy_version,
        "config_sha256": config_sha256,
        "package_sha256": package_sha256,
        "start_local": start_local,
        "end_local": end_local,
        "recorder": recorder,
        "channel": channel,
        "track": track,
        "code_version": code_version,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
