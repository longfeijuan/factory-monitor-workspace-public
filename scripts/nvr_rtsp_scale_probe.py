#!/usr/bin/env python3
"""Probe Hikvision archived RTSP playback speed using a direct Scale header."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import secrets
import socket
import time
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "connector" / "gate_nvr_service.py"
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_wall_clock(value: datetime) -> str:
    """Format local wall time for recorders that label local time with Z."""
    local_tz = timezone(timedelta(hours=8))
    return value.astimezone(local_tz).strftime("%Y-%m-%dT%H:%M:%SZ")


def md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def parse_digest(challenge: str) -> dict[str, str]:
    challenge = challenge.removeprefix("Digest ")
    return {
        key: (quoted if quoted is not None else bare)
        for key, quoted, bare in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', challenge)
    }


class RtspClient:
    def __init__(self, url: str, username: str, password: str):
        self.url = url
        self.username = username
        self.password = password
        parts = urlsplit(url)
        self.sock = socket.create_connection((parts.hostname, parts.port or 554), timeout=10)
        self.sock.settimeout(10)
        self.cseq = 0
        self.challenge: dict[str, str] | None = None
        self.session: str | None = None

    def close(self) -> None:
        # Explicitly release archive playback sessions.  Some Hikvision NVRs
        # keep a socket-only close alive long enough to exhaust the small
        # playback-session pool during long sequential audits.
        if self.session:
            try:
                self.cseq += 1
                headers = {
                    "CSeq": str(self.cseq),
                    "User-Agent": "DoorAudit/0.1",
                    "Session": self.session,
                }
                authorization = self._authorization("TEARDOWN", self.url)
                if authorization:
                    headers["Authorization"] = authorization
                payload = f"TEARDOWN {self.url} RTSP/1.0\r\n" + "".join(
                    f"{key}: {value}\r\n" for key, value in headers.items()
                ) + "\r\n"
                self.sock.sendall(payload.encode())
                # Give the recorder a brief chance to consume TEARDOWN before
                # the TCP FIN; otherwise it may retain the archive session.
                time.sleep(0.15)
            except Exception:
                pass
        self.sock.close()

    def _authorization(self, method: str, url: str) -> str | None:
        if not self.challenge:
            return None
        parts = urlsplit(url)
        digest_uri = parts.path + (("?" + parts.query) if parts.query else "")
        realm = self.challenge["realm"]
        nonce = self.challenge["nonce"]
        qop = self.challenge.get("qop", "").split(",")[0].strip()
        cnonce = secrets.token_hex(8)
        nc = "00000001"
        ha1 = md5(f"{self.username}:{realm}:{self.password}")
        ha2 = md5(f"{method}:{digest_uri}")
        if qop:
            response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        else:
            response = md5(f"{ha1}:{nonce}:{ha2}")
        fields = [
            f'username="{self.username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{digest_uri}"',
            f'response="{response}"',
        ]
        if qop:
            fields.extend([f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'])
        if self.challenge.get("opaque"):
            fields.append(f'opaque="{self.challenge["opaque"]}"')
        return "Digest " + ", ".join(fields)

    def _read_response(self) -> tuple[int, dict[str, str], bytes]:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("RTSP socket closed")
            data.extend(chunk)
        header, remainder = bytes(data).split(b"\r\n\r\n", 1)
        lines = header.decode(errors="replace").split("\r\n")
        status = int(lines[0].split()[1])
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        content_length = int(headers.get("content-length", "0"))
        body = bytearray(remainder)
        while len(body) < content_length:
            body.extend(self.sock.recv(content_length - len(body)))
        return status, headers, bytes(body[:content_length])

    def request(self, method: str, url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        for _ in range(2):
            self.cseq += 1
            request_headers = {
                "CSeq": str(self.cseq),
                "User-Agent": "DoorAudit/0.1",
            }
            if self.session:
                request_headers["Session"] = self.session
            authorization = self._authorization(method, url)
            if authorization:
                request_headers["Authorization"] = authorization
            if headers:
                request_headers.update(headers)
            payload = f"{method} {url} RTSP/1.0\r\n" + "".join(
                f"{key}: {value}\r\n" for key, value in request_headers.items()
            ) + "\r\n"
            self.sock.sendall(payload.encode())
            status, response_headers, body = self._read_response()
            if status != 401:
                if response_headers.get("session"):
                    self.session = response_headers["session"].split(";", 1)[0]
                return status, response_headers, body
            challenge = response_headers.get("www-authenticate")
            if not challenge:
                return status, response_headers, body
            self.challenge = parse_digest(challenge)
        return status, response_headers, body


def playback_url() -> tuple[str, str, str]:
    credentials, _ = MODULE.load_credentials(False, "dws")
    recorder = os.environ.get("NVR_RECORDER", "nvr-main-02")
    track = int(os.environ.get("NVR_TRACK", "101"))
    credential = credentials[recorder]
    nvr = MODULE.HikvisionNvr(recorder, credential, timeout=12)
    local_tz = timezone(timedelta(hours=8))
    start = datetime.fromisoformat(os.environ.get("NVR_START", "2026-08-01T08:00:00")).replace(tzinfo=local_tz)
    end = datetime.fromisoformat(os.environ.get("NVR_END", "2026-08-01T08:10:00")).replace(tzinfo=local_tz)
    format_time = iso_wall_clock if os.environ.get("NVR_WALL_CLOCK") == "1" else iso_utc
    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">
  <searchID>{uuid.uuid4()}</searchID>
  <trackList><trackID>{track}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>{format_time(start)}</startTime><endTime>{format_time(end)}</endTime></timeSpan></timeSpanList>
  <maxResults>5</maxResults><searchResultPostion>0</searchResultPostion>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>'''.encode()
    root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/search", body))
    item = root.find(".//{*}searchMatchItem")
    if item is None:
        raise RuntimeError("no recording")
    uri = MODULE._xml_text(item, "playbackURI")
    if not uri:
        raise RuntimeError("missing playback URI")
    parts = urlsplit(uri)
    query = dict(parse_qsl(parts.query))
    query["starttime"] = format_time(start).replace("-", "").replace(":", "")
    query["endtime"] = format_time(end).replace("-", "").replace(":", "")
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return clean_url, credential.username, credential.password


def main() -> int:
    scale = os.environ.get("NVR_SCALE", "16.0")
    url, username, password = playback_url()
    client = RtspClient(url, username, password)
    status, headers, sdp = client.request("DESCRIBE", url, {"Accept": "application/sdp"})
    if status != 200:
        raise RuntimeError(f"DESCRIBE failed: {status}")
    sdp_text = sdp.decode(errors="replace")
    controls = re.findall(r"^a=control:(.+)$", sdp_text, re.MULTILINE)
    track_control = next((value.strip() for value in controls if value.strip() != "*"), "trackID=1")
    control_url = track_control if track_control.startswith("rtsp://") else urljoin(url.rstrip("/") + "/", track_control)
    status, _, _ = client.request(
        "SETUP",
        control_url,
        {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"},
    )
    if status != 200:
        raise RuntimeError(f"SETUP failed: {status}")
    play_request_headers = {"Range": "npt=0.000-", "Scale": scale}
    for environment_key, header_name in (
        ("NVR_REQUIRE", "Require"),
        ("NVR_RATE_CONTROL", "Rate-Control"),
        ("NVR_FRAMES", "Frames"),
    ):
        if os.environ.get(environment_key):
            play_request_headers[header_name] = os.environ[environment_key]
    status, play_headers, _ = client.request("PLAY", url, play_request_headers)
    if status != 200:
        raise RuntimeError(f"PLAY failed: {status}")
    started = time.monotonic()
    first_rtp = None
    last_rtp = None
    packet_count = 0
    probe_seconds = float(os.environ.get("NVR_PROBE_SECONDS", "15"))
    while time.monotonic() - started < probe_seconds:
        marker = client.sock.recv(1)
        if not marker:
            break
        if marker != b"$":
            continue
        channel = client.sock.recv(1)[0]
        length = int.from_bytes(client.sock.recv(2), "big")
        payload = bytearray()
        while len(payload) < length:
            payload.extend(client.sock.recv(length - len(payload)))
        if channel != 0 or len(payload) < 12:
            continue
        packet_count += 1
        timestamp = int.from_bytes(payload[4:8], "big")
        first_rtp = timestamp if first_rtp is None else first_rtp
        last_rtp = timestamp
    client.close()
    elapsed_media = None
    if first_rtp is not None and last_rtp is not None:
        elapsed_media = ((last_rtp - first_rtp) & 0xFFFFFFFF) / 90000
    print(
        {
            "play_scale_response": play_headers.get("scale"),
            "requested_scale": scale,
            "wall_seconds": round(time.monotonic() - started, 2),
            "rtp_packets": packet_count,
            "rtp_media_seconds": round(elapsed_media, 2) if elapsed_media is not None else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
