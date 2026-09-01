#!/usr/bin/env python3
"""Create a canonical query identity shared by every monitoring workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate58_common import canonical_json_bytes, load_json_with_fingerprint  # noqa: E402


PROJECT_CONFIG = ROOT / "config/monitor-reproducibility-v1.json"


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("时间必须是Asia/Shanghai无偏移墙钟时间")
    return parsed.replace(microsecond=0)


def git_state(root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "exported-directory", False


def build_context(
    *,
    root: Path,
    task_type: str,
    task_policy_version: str,
    start: datetime,
    end: datetime,
    parameters: dict,
    task_config_path: Path | None,
    strict: bool,
) -> dict:
    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    project_config, project_config_sha = load_json_with_fingerprint(PROJECT_CONFIG)
    source = json.loads((root / "camera-data/current/SOURCE.json").read_text(encoding="utf-8"))
    commit, dirty = git_state(root)
    if strict and dirty:
        raise ValueError("仓库存在未提交改动；跨电脑正式结果必须使用同一干净Git提交")
    if task_config_path is None:
        task_config_sha = "git-commit-governed"
    else:
        _, task_config_sha = load_json_with_fingerprint(task_config_path)
    identity = {
        "project_policy_version": project_config["policy_version"],
        "project_config_sha256": project_config_sha,
        "task_type": task_type,
        "task_policy_version": task_policy_version,
        "task_config_sha256": task_config_sha,
        "source_package_sha256": source["zip_sha256"],
        "git_commit": commit,
        "start_local": start.isoformat(timespec="seconds"),
        "end_local": end.isoformat(timespec="seconds"),
        "parameters": parameters,
    }
    import hashlib

    query_id = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return {
        "schema_version": 1,
        "query_id": query_id,
        **identity,
        "git_dirty": dirty,
        "source_package_filename": source.get("filename", ""),
        "source_package_create_time": source.get("create_time", ""),
        "timezone": "Asia/Shanghai",
        "default_tolerances": project_config["default_tolerances"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a reproducible monitoring query")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--task-policy-version", required=True)
    parser.add_argument("--start", required=True, type=parse_time)
    parser.add_argument("--end", required=True, type=parse_time)
    parser.add_argument("--parameters-json", default="{}")
    parser.add_argument("--task-config", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        parameters = json.loads(args.parameters_json)
        if not isinstance(parameters, dict):
            raise ValueError("--parameters-json必须是JSON对象")
        output_dir = args.output_dir.expanduser().resolve()
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError("输出目录必须是全新空目录；禁止复用旧运行结果")
        output_dir.mkdir(parents=True, exist_ok=True)
        root = args.project_root.expanduser().resolve()
        context = build_context(
            root=root,
            task_type=args.task_type,
            task_policy_version=args.task_policy_version,
            start=args.start,
            end=args.end,
            parameters=parameters,
            task_config_path=args.task_config.expanduser().resolve() if args.task_config else None,
            strict=args.strict,
        )
        output = output_dir / "run-context.json"
        output.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps({"run_context": str(output), "query_id": context["query_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
