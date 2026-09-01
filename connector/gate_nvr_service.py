#!/usr/bin/env python3
"""Loopback-only, read-only Hikvision NVR connector.

On macOS, credentials are stored in Keychain. On other systems they can be
imported from an authorized DingTalk session and kept in memory for that run.
Credentials are never printed or written to project files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    ProxyHandler,
    Request,
    build_opener,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAMERA_FILE = PROJECT_ROOT / "public" / "data" / "cameras.json"
KEYCHAIN_PREFIX = "com.codex.gate-person-audit"
DINGTALK_GROUP = "黄伟工作群"
DINGTALK_START = "2026-07-17T00:00:00+08:00"
DINGTALK_END = "2026-08-03T00:00:00+08:00"
RECORDER_ORDER = ("nvr-main-01", "nvr-main-02", "nvr-main-03", "nvr-caiduo")


@dataclass(frozen=True)
class Credential:
    host: str
    username: str
    password: str


class ConnectorError(RuntimeError):
    pass


def _run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def _message_contents(payload: dict[str, Any]) -> list[str]:
    conversations = payload.get("result", {}).get("conversationMessagesList", [])
    return [
        str(message.get("content", ""))
        for conversation in conversations
        for message in conversation.get("messages", [])
    ]


def _search_group(dws: str) -> str:
    payload = _run_json([dws, "chat", "search", "--query", DINGTALK_GROUP, "--limit", "20", "--format", "json"])
    matches = [group for group in payload.get("result", {}).get("groups", []) if group.get("title") == DINGTALK_GROUP]
    if len(matches) != 1:
        raise ConnectorError("无法唯一定位黄伟工作群。")
    return str(matches[0]["openConversationId"])


def _search_messages(dws: str, group_id: str, query: str) -> list[str]:
    payload = _run_json(
        [
            dws,
            "chat",
            "message",
            "search-advanced",
            "--conversation-ids",
            group_id,
            "--query",
            query,
            "--start",
            DINGTALK_START,
            "--end",
            DINGTALK_END,
            "--limit",
            "30",
            "--format",
            "json",
        ]
    )
    return _message_contents(payload)


def import_credentials_from_dingtalk(dws: str) -> dict[str, Credential]:
    group_id = _search_group(dws)
    readonly_messages = _search_messages(dws, group_id, "只读账号")
    same_messages = _search_messages(dws, group_id, "账号密码都是一样的")
    caiduo_messages = _search_messages(dws, group_id, "材多的")

    labelled = re.compile(
        r"(?P<host>192\.168\.\d{1,3}\.\d{1,3}).*?账号\s*(?P<username>[^\s，。]+)\s+密码\s*(?P<password>[^\s，。]+)"
    )
    main_seed = next((labelled.search(content) for content in readonly_messages if labelled.search(content)), None)
    if not main_seed:
        raise ConnectorError("群记录中未找到标记为只读的NVR凭据。")

    main_username = main_seed.group("username")
    main_password = main_seed.group("password")
    main_hosts: list[str] = []
    for content in same_messages:
        main_hosts.extend(re.findall(r"192\.168\.0\.\d{1,3}", content))
    main_hosts = sorted(set(main_hosts), key=lambda value: tuple(int(part) for part in value.split(".")))
    if len(main_hosts) != 3:
        raise ConnectorError("群记录中的主厂区NVR数量不是3台。")

    caiduo_pattern = re.compile(
        r"(?P<host>192\.168\.88\.\d{1,3})\s+(?P<username>[^\s，。]+)\s+(?P<password>[^\s，。]+).*?材多"
    )
    caiduo_match = next((caiduo_pattern.search(content) for content in caiduo_messages if caiduo_pattern.search(content)), None)
    if not caiduo_match:
        raise ConnectorError("群记录中未找到材多NVR凭据。")

    return {
        "nvr-main-01": Credential(main_hosts[0], main_username, main_password),
        "nvr-main-02": Credential(main_hosts[1], main_username, main_password),
        "nvr-main-03": Credential(main_hosts[2], main_username, main_password),
        "nvr-caiduo": Credential(
            caiduo_match.group("host"), caiduo_match.group("username"), caiduo_match.group("password")
        ),
    }


def _keychain_service(recorder: str) -> str:
    return f"{KEYCHAIN_PREFIX}.{recorder}"


def keychain_available() -> bool:
    return platform.system() == "Darwin" and Path("/usr/bin/security").is_file()


def load_keychain_credentials() -> dict[str, Credential] | None:
    if not keychain_available():
        return None
    result: dict[str, Credential] = {}
    for recorder in RECORDER_ORDER:
        completed = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", _keychain_service(recorder), "-a", "connection", "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            return None
        try:
            payload = json.loads(completed.stdout)
            result[recorder] = Credential(
                host=str(payload["host"]), username=str(payload["username"]), password=str(payload["password"])
            )
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ConnectorError("钥匙串中的NVR连接项格式无效。") from error
    return result


def save_keychain_credentials(credentials: dict[str, Credential]) -> None:
    if not keychain_available():
        raise ConnectorError("当前系统没有可用的macOS钥匙串。")
    for recorder, credential in credentials.items():
        secret = json.dumps(
            {"host": credential.host, "username": credential.username, "password": credential.password},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        completed = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-s",
                _keychain_service(recorder),
                "-a",
                "connection",
                "-w",
                secret,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if completed.returncode != 0:
            raise ConnectorError("无法把NVR连接信息保存到macOS钥匙串。")


def load_credentials(import_from_dingtalk: bool, dws: str) -> tuple[dict[str, Credential], str]:
    stored = load_keychain_credentials()
    if stored:
        return stored, "keychain"
    if not import_from_dingtalk:
        raise ConnectorError("本机没有可用的NVR连接项；首次运行请加 --import-from-dingtalk。")
    imported = import_credentials_from_dingtalk(dws)
    if keychain_available():
        save_keychain_credentials(imported)
        return imported, "dingtalk-to-keychain"
    return imported, "dingtalk-memory"


def default_dws() -> str:
    configured = os.environ.get("DWS_BIN")
    if configured:
        return configured
    discovered = shutil.which("dws")
    if discovered:
        return discovered
    return str(Path.home() / ".local" / "bin" / "dws")


def _xml_text(element: ET.Element, name: str) -> str | None:
    found = element.find(f".//{{*}}{name}")
    return found.text.strip() if found is not None and found.text else None


class HikvisionNvr:
    def __init__(self, recorder: str, credential: Credential, timeout: float = 4.0):
        self.recorder = recorder
        self.credential = credential
        self.timeout = timeout
        manager = HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, f"http://{credential.host}", credential.username, credential.password)
        self.opener = build_opener(ProxyHandler({}), HTTPDigestAuthHandler(manager))

    def request(self, path: str, body: bytes | None = None) -> bytes:
        request = Request(
            f"http://{self.credential.host}{path}",
            data=body,
            method="POST" if body is not None else "GET",
            headers={"accept": "application/xml", "content-type": "application/xml"},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read(2 * 1024 * 1024)
        except HTTPError as error:
            raise ConnectorError(f"{self.recorder}只读接口返回HTTP {error.code}。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise ConnectorError(f"{self.recorder}只读接口不可达。") from error

    def device_summary(self, camera_count: int) -> dict[str, Any]:
        root = ET.fromstring(self.request("/ISAPI/System/deviceInfo"))
        return {
            "recorder": self.recorder,
            "reachable": True,
            "model": _xml_text(root, "model") or "unknown",
            "firmware": _xml_text(root, "firmwareVersion") or "unknown",
            "cameraCount": camera_count,
        }

    def clock_status(self) -> str:
        try:
            root = ET.fromstring(self.request("/ISAPI/System/time"))
            raw = _xml_text(root, "localTime") or _xml_text(root, "time")
            if not raw:
                return "unknown"
            device_time = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if device_time.tzinfo is None:
                return "unverified"
            delta = abs((datetime.now(timezone.utc) - device_time.astimezone(timezone.utc)).total_seconds())
            return "validated" if delta <= 120 else "unverified"
        except (ConnectorError, ET.ParseError, ValueError):
            return "unknown"

    def search_recordings(self, channel: int, start: datetime, end: datetime) -> list[dict[str, str]]:
        track_id = channel * 100 + 1
        start_utc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        results: list[dict[str, str]] = []
        position = 0

        for _ in range(10):
            search_id = str(uuid.uuid4())
            body = f"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">
  <searchID>{search_id}</searchID>
  <trackList><trackID>{track_id}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>{start_utc}</startTime><endTime>{end_utc}</endTime></timeSpan></timeSpanList>
  <maxResults>40</maxResults>
  <searchResultPostion>{position}</searchResultPostion>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>""".encode()
            root = ET.fromstring(self.request("/ISAPI/ContentMgmt/search", body))
            items = root.findall(".//{*}searchMatchItem")
            for item in items:
                item_start = _xml_text(item, "startTime")
                item_end = _xml_text(item, "endTime")
                playback_uri = _xml_text(item, "playbackURI") or ""
                if item_start and item_end:
                    results.append(
                        {
                            "start": item_start,
                            "end": item_end,
                            "evidenceId": "ev-" + hashlib.sha256(playback_uri.encode()).hexdigest()[:16],
                        }
                    )
            status = (_xml_text(root, "responseStatusStrg") or "OK").upper()
            if status != "MORE" or not items:
                break
            position += len(items)
        return results


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def _merge_coverage(
    segments: list[dict[str, str]], start: datetime, end: datetime
) -> tuple[str, list[dict[str, str]], list[str]]:
    intervals: list[tuple[datetime, datetime, str]] = []
    for segment in segments:
        try:
            segment_start = max(parse_datetime(segment["start"]), start)
            segment_end = min(parse_datetime(segment["end"]), end)
        except (KeyError, ValueError):
            continue
        if segment_end > segment_start:
            intervals.append((segment_start, segment_end, segment["evidenceId"]))
    intervals.sort(key=lambda item: item[0])

    merged: list[tuple[datetime, datetime]] = []
    evidence_ids: list[str] = []
    for interval_start, interval_end, evidence_id in intervals:
        if evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
        if merged and interval_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], interval_end))
        else:
            merged.append((interval_start, interval_end))

    gaps: list[dict[str, str]] = []
    cursor = start
    for interval_start, interval_end in merged:
        if interval_start > cursor:
            gaps.append({"start": cursor.isoformat(), "end": interval_start.isoformat(), "reason": "recording_gap"})
        cursor = max(cursor, interval_end)
    if cursor < end:
        gaps.append({"start": cursor.isoformat(), "end": end.isoformat(), "reason": "recording_gap"})

    if not merged:
        return "missing", gaps, evidence_ids
    return ("available" if not gaps else "partial"), gaps, evidence_ids


def load_camera_map() -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    cameras = json.loads(CAMERA_FILE.read_text(encoding="utf-8"))
    camera_map = {
        str(camera["id"]): {"recorder": str(camera["recorder"]), "channel": int(camera["channel"])} for camera in cameras
    }
    counts: dict[str, int] = {}
    for camera in cameras:
        counts[str(camera["recorder"])] = counts.get(str(camera["recorder"]), 0) + 1
    return camera_map, counts


class GateApplication:
    def __init__(self, credentials: dict[str, Credential], credential_source: str):
        self.camera_map, self.camera_counts = load_camera_map()
        self.clients = {recorder: HikvisionNvr(recorder, credential) for recorder, credential in credentials.items()}
        self.credential_source = credential_source

    def health(self) -> tuple[int, dict[str, Any]]:
        def inspect(recorder: str) -> dict[str, Any]:
            try:
                return self.clients[recorder].device_summary(self.camera_counts.get(recorder, 0))
            except (ConnectorError, ET.ParseError):
                return {"recorder": recorder, "reachable": False, "cameraCount": self.camera_counts.get(recorder, 0)}

        with ThreadPoolExecutor(max_workers=4) as executor:
            summaries = list(executor.map(inspect, RECORDER_ORDER))
        healthy = all(item["reachable"] for item in summaries)
        return (200 if healthy else 503), {
            "status": "ok" if healthy else "degraded",
            "credentialSource": self.credential_source,
            "recorders": summaries,
            "cameraCount": len(self.camera_map),
        }

    def coverage(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        camera_id = payload.get("cameraId")
        if not isinstance(camera_id, str) or camera_id not in self.camera_map:
            return 400, {"error": "cameraId不在获批目录中。"}
        try:
            start = parse_datetime(str(payload["start"]))
            end = parse_datetime(str(payload["end"]))
        except (KeyError, ValueError):
            return 400, {"error": "start和end必须是带时区的ISO时间。"}
        if end <= start or (end - start).total_seconds() > 86400:
            return 400, {"error": "查询窗口必须大于0且不超过24小时。"}

        mapping = self.camera_map[camera_id]
        client = self.clients[mapping["recorder"]]
        try:
            segments = client.search_recordings(mapping["channel"], start, end)
            status, gaps, evidence_ids = _merge_coverage(segments, start, end)
            note = (
                "录像机返回了覆盖该时段的录像段；仍需人工核对画面。"
                if status == "available"
                else "录像机返回的目标时段存在缺口；缺口不证明事件没有发生。"
                if status == "partial"
                else "录像机未返回目标时段录像段；该结果不证明事件没有发生。"
            )
            return 200, {
                "cameraId": camera_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "status": status,
                "clockStatus": client.clock_status(),
                "decodeStatus": "unknown",
                "evidenceIds": evidence_ids[:100],
                "gaps": gaps[:100],
                "note": note,
            }
        except (ConnectorError, ET.ParseError):
            return 503, {
                "cameraId": camera_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "status": "unknown",
                "clockStatus": "unknown",
                "decodeStatus": "unknown",
                "evidenceIds": [],
                "gaps": [],
                "note": "只读录像查询失败；未知不等于没有录像。",
            }


class GateRequestHandler(BaseHTTPRequestHandler):
    server_version = "GateNvrConnector/0.1"

    @property
    def app(self) -> GateApplication:
        return self.server.app  # type: ignore[attr-defined]

    def _authorized(self) -> bool:
        token = os.environ.get("GATE_CONNECTOR_TOKEN", "")
        return not token or self.headers.get("authorization") == f"Bearer {token}"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if not self._authorized():
            self._write_json(401, {"error": "unauthorized"})
            return
        if self.path != "/health":
            self._write_json(404, {"error": "not found"})
            return
        status, payload = self.app.health()
        self._write_json(status, payload)

    def do_POST(self) -> None:
        if not self._authorized():
            self._write_json(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/coverage":
            self._write_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self._write_json(400, {"error": "invalid json"})
            return
        status, result = self.app.coverage(payload)
        self._write_json(status, result)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="GatePersonAudit只读NVR连接器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--import-from-dingtalk", action="store_true")
    parser.add_argument("--check", action="store_true", help="只检查连接后退出")
    parser.add_argument("--dws", default=default_dws())
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("连接器只允许绑定本机回环地址。", file=sys.stderr)
        return 2

    try:
        credentials, source = load_credentials(args.import_from_dingtalk, args.dws)
        application = GateApplication(credentials, source)
        if args.check:
            status, payload = application.health()
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if status == 200 else 1
        server = ThreadingHTTPServer((args.host, args.port), GateRequestHandler)
        server.app = application  # type: ignore[attr-defined]
        print(f"Gate NVR connector listening on {args.host}:{args.port}; credentials={source}", flush=True)
        server.serve_forever()
    except (ConnectorError, subprocess.SubprocessError, json.JSONDecodeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
