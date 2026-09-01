#!/usr/bin/env python3
"""Read-only sampler for a Hikvision archive segment over ISAPI HTTP download."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

import av
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "connector" / "gate_nvr_service.py"
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


LOCAL_TZ = timezone(timedelta(hours=8))


def wall_clock(value: datetime) -> str:
    """Format the local wall-clock value expected by this recorder."""
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_wall_clock(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y%m%dT%H%M%SZ")


def parse_local(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def parse_recorder_wall_clock(value: str) -> datetime:
    """Treat the recorder's Z-suffixed archive timestamps as local wall time."""
    return datetime.fromisoformat(value.removesuffix("Z")).replace(tzinfo=LOCAL_TZ)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorder", default="nvr-main-02")
    parser.add_argument("--track", type=int, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--step", type=float, default=5.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-width", type=int, default=1280)
    args = parser.parse_args()

    start = parse_local(args.start)
    end = parse_local(args.end)
    if end <= start:
        parser.error("--end must be after --start")

    credentials, _ = MODULE.load_credentials(False, "dws")
    credential = credentials[args.recorder]
    nvr = MODULE.HikvisionNvr(args.recorder, credential, timeout=20)
    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">
  <searchID>{uuid.uuid4()}</searchID>
  <trackList><trackID>{args.track}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>{wall_clock(start)}</startTime><endTime>{wall_clock(end)}</endTime></timeSpan></timeSpanList>
  <maxResults>20</maxResults><searchResultPostion>0</searchResultPostion>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>'''.encode()
    root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/search", body))
    items = root.findall(".//{*}searchMatchItem")
    if not items:
        raise RuntimeError("no recording")
    item = items[0]
    for candidate in items:
        item_start = MODULE._xml_text(candidate, "startTime")
        item_end = MODULE._xml_text(candidate, "endTime")
        if not item_start or not item_end:
            continue
        if parse_recorder_wall_clock(item_start) <= start < parse_recorder_wall_clock(item_end):
            item = candidate
            break
    item_start_text = MODULE._xml_text(item, "startTime")
    item_end_text = MODULE._xml_text(item, "endTime")
    if not item_start_text or not item_end_text:
        raise RuntimeError("recording item missing time span")
    recording_start = parse_recorder_wall_clock(item_start_text)
    uri = MODULE._xml_text(item, "playbackURI")
    if not uri:
        raise RuntimeError("missing playback URI")
    parts = urlsplit(uri)
    query = dict(parse_qsl(parts.query))
    query["starttime"] = compact_wall_clock(start)
    query["endtime"] = compact_wall_clock(end)
    uri = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

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

    args.out.mkdir(parents=True, exist_ok=True)
    response = nvr.opener.open(request, timeout=30)
    container = av.open(response, format="mpeg")
    stream = container.streams.video[0]
    target_offset = max(0.0, (start - recording_start).total_seconds())
    target_end_offset = (end - recording_start).total_seconds()
    stream.codec_context.skip_frame = "NONKEY"
    saved: list[dict[str, object]] = []
    first_time = None
    next_offset = target_offset
    full_decode = False
    try:
        for frame in container.decode(stream):
            frame_time = float(frame.time or 0.0)
            first_time = frame_time if first_time is None else first_time
            offset = frame_time - first_time
            if not full_decode and offset >= max(0.0, target_offset - 5.0):
                stream.codec_context.skip_frame = "DEFAULT"
                full_decode = True
                continue
            if offset > target_end_offset + 1.0:
                break
            if offset + 1e-3 < next_offset:
                continue
            image: Image.Image = frame.to_image()
            if image.width > args.max_width:
                image = image.resize(
                    (args.max_width, round(image.height * args.max_width / image.width)),
                    Image.Resampling.LANCZOS,
                )
            stamp = recording_start + timedelta(seconds=offset)
            path = args.out / f"{stamp.strftime('%Y%m%d-%H%M%S')}.jpg"
            image.save(path, "JPEG", quality=88)
            saved.append({"offset": round(offset, 3), "local": stamp.isoformat(), "image": str(path)})
            next_offset += args.step
    finally:
        container.close()
        response.close()

    (args.out / "frames.json").write_text(
        json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "frames": len(saved),
                "out": str(args.out),
                "recording_start": item_start_text,
                "recording_end": item_end_text,
                "target_offset": target_offset,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
