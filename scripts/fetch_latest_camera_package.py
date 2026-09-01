#!/usr/bin/env python3
"""Find and optionally install the newest sanitized camera package from DingTalk."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GROUP_NAME = "黄伟工作群"
PACKAGE_RE = re.compile(r"公司摄像头主管协作包_(\d{8})(?:_r(\d+))?\.zip")
FILE_ID_RE = re.compile(r"fileId:\s*([A-Za-z0-9_-]+)")


def default_dws() -> str:
    return os.environ.get("DWS_BIN") or shutil.which("dws") or str(Path.home() / ".local" / "bin" / "dws")


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    return json.loads(completed.stdout)


def find_group(dws: str) -> str:
    payload = run_json([dws, "chat", "search", "--query", GROUP_NAME, "--limit", "20", "--format", "json"])
    groups = [item for item in payload.get("result", {}).get("groups", []) if item.get("title") == GROUP_NAME]
    if len(groups) != 1:
        raise RuntimeError("无法唯一定位黄伟工作群")
    return str(groups[0]["openConversationId"])


def latest_message(dws: str, group_id: str, days: int) -> dict[str, str]:
    now = datetime.now().astimezone()
    payload = run_json(
        [
            dws,
            "chat",
            "message",
            "search-advanced",
            "--conversation-ids",
            group_id,
            "--query",
            "公司摄像头主管协作包",
            "--start",
            (now - timedelta(days=days)).isoformat(timespec="seconds"),
            "--end",
            (now + timedelta(days=1)).isoformat(timespec="seconds"),
            "--limit",
            "100",
            "--format",
            "json",
        ]
    )
    messages = [
        message
        for conversation in payload.get("result", {}).get("conversationMessagesList", [])
        for message in conversation.get("messages", [])
    ]
    candidates: list[dict[str, str]] = []
    for message in messages:
        content = str(message.get("content", ""))
        filename = PACKAGE_RE.search(content)
        file_id = FILE_ID_RE.search(content)
        if filename and file_id:
            candidates.append(
                {
                    "filename": filename.group(0),
                    "file_id": file_id.group(1),
                    "create_time": str(message.get("createTime", "")),
                }
            )
    if not candidates:
        raise RuntimeError("群内未找到摄像头主管协作包")
    return max(candidates, key=lambda item: (item["filename"], item["create_time"]))


def safe_extract(zip_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or info.is_dir() and info.filename.startswith("/"):
                raise RuntimeError("ZIP包含不安全路径")
        archive.extractall(destination)
    manifests = list(destination.rglob("MANIFEST.sha256"))
    if len(manifests) != 1:
        raise RuntimeError("ZIP中未找到唯一MANIFEST.sha256")
    return manifests[0].parent


def verify_manifest(package_root: Path) -> None:
    for line in (package_root / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(maxsplit=1)
        target = package_root / relative.removeprefix("./")
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"资料包校验失败：{relative}")


def install(package_root: Path, metadata: dict[str, str], zip_sha256: str) -> None:
    camera_data = ROOT / "camera-data"
    current = camera_data / "current"
    staging = camera_data / ".current-staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(package_root, staging)
    (staging / "SOURCE.json").write_text(
        json.dumps({**metadata, "zip_sha256": zip_sha256}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if current.exists():
        shutil.rmtree(current)
    staging.rename(current)


def main() -> int:
    parser = argparse.ArgumentParser(description="读取黄伟工作群最新脱敏监控资料包")
    parser.add_argument("--dws", default=default_dws())
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--apply", action="store_true", help="校验通过后替换camera-data/current")
    args = parser.parse_args()

    group_id = find_group(args.dws)
    latest = latest_message(args.dws, group_id, args.days)
    print(f"最新资料：{latest['filename']}，发布时间：{latest['create_time']}")
    if not args.apply:
        return 0

    with tempfile.TemporaryDirectory(prefix="camera-package-") as temporary:
        temp = Path(temporary)
        zip_path = temp / latest["filename"]
        subprocess.run(
            [args.dws, "drive", "download", "--node", latest["file_id"], "--output", str(zip_path), "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        zip_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        package_root = safe_extract(zip_path, temp / "unpacked")
        verify_manifest(package_root)
        install(package_root, latest, zip_sha256)
    print("已更新camera-data/current；请运行python3 scripts/sync_camera_catalog.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
