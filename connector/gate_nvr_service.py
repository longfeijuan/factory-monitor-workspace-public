#!/usr/bin/env python3
"""Loopback-only, read-only Hikvision NVR connector.

On Windows, credentials are stored in Windows Credential Manager. On macOS,
they are stored in Keychain. An authorized data maintainer can still import
them from DingTalk, but ordinary users can enter them locally without access
to the source group. Credentials are never printed or written to project files.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import ipaddress
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
from ctypes import wintypes
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
NVR_ENDPOINT_FILE = PROJECT_ROOT / "config" / "nvr-endpoints.json"
KEYCHAIN_PREFIX = "com.codex.gate-person-audit"
WINDOWS_CREDENTIAL_PREFIX = "FactoryMonitor/NVR"
DINGTALK_GROUP = "黄伟工作群"
DINGTALK_START = "2026-07-17T00:00:00+08:00"
DINGTALK_END = "2026-08-03T00:00:00+08:00"
RECORDER_ORDER = ("nvr-main-01", "nvr-main-02", "nvr-main-03", "nvr-caiduo")
RECORDER_LABELS = {
    "nvr-main-01": "主厂区录像机1",
    "nvr-main-02": "主厂区录像机2",
    "nvr-main-03": "主厂区录像机3",
    "nvr-caiduo": "材多录像机",
}

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _WindowsCredential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


@dataclass(frozen=True)
class Credential:
    host: str
    username: str
    password: str


class ConnectorError(RuntimeError):
    pass


def load_builtin_hosts() -> dict[str, str]:
    try:
        payload = json.loads(NVR_ENDPOINT_FILE.read_text(encoding="utf-8"))
        recorders = payload["recorders"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConnectorError("项目内置NVR地址目录缺失或格式无效。") from error
    if payload.get("schema_version") != 1 or set(recorders) != set(RECORDER_ORDER):
        raise ConnectorError("项目内置NVR地址目录必须完整包含4台录像机。")
    result: dict[str, str] = {}
    for recorder in RECORDER_ORDER:
        entry = recorders[recorder]
        if not isinstance(entry, dict):
            raise ConnectorError(f"{recorder}的内置地址项格式无效。")
        host = str(entry.get("host", "")).strip()
        expected_group = "caiduo" if recorder == "nvr-caiduo" else "main"
        if entry.get("credential_group") != expected_group:
            raise ConnectorError(f"{recorder}的内置凭据分组无效。")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ConnectorError(f"{recorder}的内置地址不是有效IP地址。") from None
        if address.version != 4 or not address.is_private:
            raise ConnectorError(f"{recorder}的内置地址不是公司私有IPv4地址。")
        result[recorder] = host
    return result


def _validate_credential(recorder: str, credential: Credential) -> None:
    if recorder not in RECORDER_ORDER:
        raise ConnectorError("NVR连接项名称无效。")
    host = credential.host.strip()
    username = credential.username.strip()
    password = credential.password
    if not host or not username or not password:
        raise ConnectorError(f"{recorder}的地址、只读用户名或密码为空。")
    if any(character.isspace() for character in host) or "://" in host or "/" in host:
        raise ConnectorError(f"{recorder}的地址格式无效；只填写IP地址或主机名。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
            raise ConnectorError(f"{recorder}的地址格式无效。") from None
    else:
        if address.version != 4 or not address.is_private:
            raise ConnectorError(f"{recorder}必须填写公司内网或VPN可访问的私有IPv4地址。")
    expected_host = load_builtin_hosts()[recorder]
    if host != expected_host:
        raise ConnectorError(f"{recorder}必须使用项目内置地址目录中的地址。")
    if "\x00" in username or "\x00" in password:
        raise ConnectorError(f"{recorder}的用户名或密码包含无效字符。")


def _validate_credential_set(credentials: dict[str, Credential]) -> None:
    if set(credentials) != set(RECORDER_ORDER):
        raise ConnectorError("必须完整提供4台NVR连接项。")
    for recorder in RECORDER_ORDER:
        _validate_credential(recorder, credentials[recorder])


def windows_credential_manager_available() -> bool:
    return platform.system() == "Windows"


def _windows_credential_api():
    if not windows_credential_manager_available():
        raise ConnectorError("当前系统没有可用的Windows凭据管理器。")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_WindowsCredential)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_WindowsCredential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _windows_credential_target(recorder: str) -> str:
    return f"{WINDOWS_CREDENTIAL_PREFIX}/{recorder}"


def _read_windows_secret(target: str) -> str | None:
    api = _windows_credential_api()
    pointer = ctypes.POINTER(_WindowsCredential)()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error_code = ctypes.get_last_error()
        if error_code == ERROR_NOT_FOUND:
            return None
        raise ConnectorError(f"Windows凭据管理器读取失败（错误码{error_code}）。")
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    except (UnicodeDecodeError, ValueError) as error:
        raise ConnectorError("Windows凭据管理器中的NVR连接项格式无效。") from error
    finally:
        api.CredFree(pointer)


def _write_windows_secret(target: str, secret: str) -> None:
    api = _windows_credential_api()
    raw = secret.encode("utf-16-le")
    blob = ctypes.create_string_buffer(raw)
    credential = _WindowsCredential()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "Factory Monitor read-only NVR connection"
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "connection"
    if not api.CredWriteW(ctypes.byref(credential), 0):
        error_code = ctypes.get_last_error()
        raise ConnectorError(f"Windows凭据管理器写入失败（错误码{error_code}）。")


def load_windows_credentials() -> dict[str, Credential] | None:
    if not windows_credential_manager_available():
        return None
    result: dict[str, Credential] = {}
    builtin_hosts = load_builtin_hosts()
    for recorder in RECORDER_ORDER:
        secret = _read_windows_secret(_windows_credential_target(recorder))
        if secret is None:
            return None
        try:
            payload = json.loads(secret)
            result[recorder] = Credential(
                host=builtin_hosts[recorder],
                username=str(payload["username"]),
                password=str(payload["password"]),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ConnectorError("Windows凭据管理器中的NVR连接项格式无效。") from error
    _validate_credential_set(result)
    return result


def save_windows_credentials(credentials: dict[str, Credential]) -> None:
    if not windows_credential_manager_available():
        raise ConnectorError("当前系统没有可用的Windows凭据管理器。")
    _validate_credential_set(credentials)
    for recorder in RECORDER_ORDER:
        credential = credentials[recorder]
        secret = json.dumps(
            {"username": credential.username, "password": credential.password},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _write_windows_secret(_windows_credential_target(recorder), secret)


def setup_windows_credentials_interactive() -> dict[str, Credential]:
    if not windows_credential_manager_available():
        raise ConnectorError("安全手动录入入口目前只支持Windows凭据管理器。")
    print("4台NVR地址已经内置，不需要手动输入地址。密码输入时屏幕不会显示字符。")
    print("账号和密码只保存到当前Windows用户的凭据管理器，不会进入Git、报告或聊天记录。")
    builtin_hosts = load_builtin_hosts()
    main_username = input("主厂区3台NVR只读用户名: ").strip()
    main_password = getpass.getpass("主厂区3台NVR只读密码: ")
    caiduo_username = input("材多NVR只读用户名（回车沿用主厂区）: ").strip() or main_username
    caiduo_password = getpass.getpass("材多NVR只读密码（回车沿用主厂区）: ") or main_password
    result = {
        recorder: Credential(
            host=builtin_hosts[recorder],
            username=caiduo_username if recorder == "nvr-caiduo" else main_username,
            password=caiduo_password if recorder == "nvr-caiduo" else main_password,
        )
        for recorder in RECORDER_ORDER
    }
    _validate_credential_set(result)
    save_windows_credentials(result)
    print("NVR_CREDENTIAL_SETUP=PASS; store=windows-credential-manager; recorders=4")
    return result


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
    builtin_hosts = load_builtin_hosts()
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
                host=builtin_hosts[recorder], username=str(payload["username"]), password=str(payload["password"])
            )
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ConnectorError("钥匙串中的NVR连接项格式无效。") from error
    return result


def save_keychain_credentials(credentials: dict[str, Credential]) -> None:
    if not keychain_available():
        raise ConnectorError("当前系统没有可用的macOS钥匙串。")
    for recorder, credential in credentials.items():
        secret = json.dumps(
            {"username": credential.username, "password": credential.password},
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


def load_stored_credentials() -> tuple[dict[str, Credential] | None, str | None]:
    windows_stored = load_windows_credentials()
    if windows_stored:
        return windows_stored, "windows-credential-manager"
    keychain_stored = load_keychain_credentials()
    if keychain_stored:
        return keychain_stored, "keychain"
    return None, None


def load_credentials(import_from_dingtalk: bool, dws: str) -> tuple[dict[str, Credential], str]:
    stored, source = load_stored_credentials()
    if stored:
        assert source is not None
        return stored, source
    if not import_from_dingtalk:
        raise ConnectorError(
            "本机没有可用的NVR连接项；Windows请运行 --setup-credentials，"
            "资料同步人也可以使用 --import-from-dingtalk。"
        )
    imported = import_credentials_from_dingtalk(dws)
    _validate_credential_set(imported)
    if windows_credential_manager_available():
        save_windows_credentials(imported)
        return imported, "dingtalk-to-windows-credential-manager"
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
    parser.add_argument(
        "--setup-credentials",
        action="store_true",
        help="使用内置4台地址，在本机隐藏录入只读用户名和密码并保存到Windows凭据管理器",
    )
    parser.add_argument(
        "--credential-status",
        action="store_true",
        help="只检查本机是否已有完整NVR连接项，不连接NVR",
    )
    parser.add_argument(
        "--import-from-dingtalk",
        action="store_true",
        help="仅供有权访问内部发布群的资料同步人导入凭据",
    )
    parser.add_argument("--check", action="store_true", help="只检查连接后退出")
    parser.add_argument("--dws", default=default_dws())
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("连接器只允许绑定本机回环地址。", file=sys.stderr)
        return 2

    try:
        setup_credentials = setup_windows_credentials_interactive() if args.setup_credentials else None
        if args.credential_status:
            stored, source = load_stored_credentials()
            if stored:
                print(f"NVR_CREDENTIAL_STATUS=READY; source={source}; recorders={len(stored)}")
                return 0
            print("NVR_CREDENTIAL_STATUS=MISSING; run=SETUP-NVR-CREDENTIALS.cmd")
            return 3
        if setup_credentials is not None:
            credentials, source = setup_credentials, "windows-credential-manager"
            if not args.check:
                return 0
        else:
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
