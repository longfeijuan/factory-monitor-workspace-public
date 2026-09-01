#!/usr/bin/env python3
"""Measure keyframe decode speed while streaming a Hikvision recording download."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request

import av
import numpy as np


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
    target_seconds = float(os.environ.get("NVR_TARGET_SECONDS", "600"))
    credentials, _ = MODULE.load_credentials(False, "dws")
    credential = credentials["nvr-main-02"]
    nvr = MODULE.HikvisionNvr("nvr-main-02", credential, timeout=12)
    local_tz = timezone(timedelta(hours=8))
    start = datetime(2026, 8, 1, 8, 0, tzinfo=local_tz)
    end = start + timedelta(minutes=5)
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
    uri = MODULE._xml_text(item, "playbackURI")
    if not uri:
        raise RuntimeError("missing playback URI")
    download_body = f'''<?xml version="1.0" encoding="UTF-8"?>
<downloadRequest xmlns="http://www.isapi.org/ver20/XMLSchema" version="1.0">
  <playbackURI>{uri.replace('&', '&amp;')}</playbackURI>
</downloadRequest>'''.encode()
    request = Request(
        f"http://{credential.host}/ISAPI/ContentMgmt/download",
        data=download_body,
        method="POST",
        headers={"Content-Type": "application/xml"},
    )
    started = time.monotonic()
    response = nvr.opener.open(request, timeout=30)
    container = av.open(response, format="mpeg")
    stream = container.streams.video[0]
    hardware_decoder = None
    if os.environ.get("NVR_DECODER") == "videotoolbox":
        hardware_decoder = av.CodecContext.create("hevc_videotoolbox", "r")
        hardware_decoder.extradata = stream.codec_context.extradata
    if os.environ.get("NVR_DEMUX_ONLY") == "1":
        per_second: dict[int, int] = {}
        keyframes = 0
        first_packet_time = None
        last_packet_time = None
        for packet in container.demux(stream):
            if packet.pts is None:
                continue
            packet_time = float(packet.pts * packet.time_base)
            first_packet_time = packet_time if first_packet_time is None else first_packet_time
            last_packet_time = packet_time
            relative = packet_time - first_packet_time
            per_second[int(relative)] = per_second.get(int(relative), 0) + packet.size
            keyframes += int(packet.is_keyframe)
            if relative >= target_seconds:
                break
        container.close()
        response.close()
        values = np.array(list(per_second.values()), dtype=np.float64)
        print(
            {
                "wall_seconds": round(time.monotonic() - started, 2),
                "mode": "demux_only",
                "media_seconds": round((last_packet_time or 0) - (first_packet_time or 0), 2),
                "keyframes": keyframes,
                "bytes_per_second_percentiles": {
                    str(p): round(float(np.percentile(values, p)), 1)
                    for p in (0, 25, 50, 75, 90, 95, 99, 100)
                },
            }
        )
        return 0
    skip_frame = os.environ.get("NVR_SKIP_FRAME", "NONKEY")
    stream.codec_context.skip_frame = skip_frame
    first_time = None
    last_time = None
    frame_count = 0
    decoded = (
        (frame for packet in container.demux(stream) for frame in hardware_decoder.decode(packet))
        if hardware_decoder is not None
        else container.decode(stream)
    )
    for frame in decoded:
        frame_count += 1
        first_time = frame.time if first_time is None else first_time
        last_time = frame.time
        if first_time is not None and last_time is not None and last_time - first_time >= target_seconds:
            break
    container.close()
    response.close()
    print(
        {
            "wall_seconds": round(time.monotonic() - started, 2),
            "skip_frame": skip_frame,
            "decoder": "videotoolbox" if hardware_decoder is not None else "default",
            "keyframes": frame_count,
            "media_seconds": round((last_time or 0) - (first_time or 0), 2),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
