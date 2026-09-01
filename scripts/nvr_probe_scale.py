#!/usr/bin/env python3
"""Probe whether archived RTSP playback honors a custom RTSP Scale header."""

from __future__ import annotations

import importlib.util
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import av


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "connector" / "gate_nvr_service.py"
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    credentials, _ = MODULE.load_credentials(False, "dws")
    credential = credentials["nvr-main-02"]
    nvr = MODULE.HikvisionNvr("nvr-main-02", credential, timeout=12)
    local_tz = timezone(timedelta(hours=8))
    start = datetime(2026, 8, 1, 8, 0, tzinfo=local_tz)
    end = start + timedelta(minutes=10)
    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">
  <searchID>{uuid.uuid4()}</searchID>
  <trackList><trackID>101</trackID></trackList>
  <timeSpanList><timeSpan><startTime>{iso_utc(start)}</startTime><endTime>{iso_utc(end)}</endTime></timeSpan></timeSpanList>
  <maxResults>5</maxResults><searchResultPostion>0</searchResultPostion>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>'''.encode()
    root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/search", body))
    item = root.find(".//{*}searchMatchItem")
    if item is None:
        raise RuntimeError("no recording")
    playback_uri = MODULE._xml_text(item, "playbackURI")
    if not playback_uri:
        raise RuntimeError("missing playback URI")
    parts = urlsplit(playback_uri)
    query = dict(parse_qsl(parts.query))
    query["starttime"] = iso_utc(start).replace("-", "").replace(":", "")
    query["endtime"] = iso_utc(end).replace("-", "").replace(":", "")
    netloc = (
        f"{quote(credential.username, safe='')}:{quote(credential.password, safe='')}@{parts.hostname}"
        + (f":{parts.port}" if parts.port else "")
    )
    url = urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))

    started = time.monotonic()
    frame_count = 0
    last_time = None
    container = av.open(
        url,
        options={
            "rtsp_transport": "tcp",
            "stimeout": "7000000",
            "headers": "Scale: 16.0\r\n",
        },
        timeout=8,
    )
    stream = container.streams.video[0]
    stream.codec_context.skip_frame = "NONKEY"
    for frame in container.decode(stream):
        frame_count += 1
        last_time = frame.time
        if time.monotonic() - started > 18 or (last_time and last_time > 300):
            break
    container.close()
    print(
        {
            "wall_seconds": round(time.monotonic() - started, 1),
            "keyframes": frame_count,
            "stream_seconds": last_time,
        },
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
