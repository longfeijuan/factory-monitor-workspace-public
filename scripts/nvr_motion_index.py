#!/usr/bin/env python3
"""Build a read-only per-second motion proxy index from Hikvision archives.

The recorder's download stream is demuxed without decoding.  Bytes emitted per
media second are a useful first-pass proxy for scene changes on these variable
bit-rate HEVC cameras.  This index is only used to narrow later visual review;
it is not treated as evidence by itself.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request

import av


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "connector" / "gate_nvr_service.py"
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
LOCAL_TZ = timezone(timedelta(hours=8))


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_wall_clock(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_wall_clock(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=LOCAL_TZ)


def search(nvr, track: int, start: datetime, end: datetime, nvr_wall_clock: bool) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    position = 0
    search_id = uuid.uuid4()
    while True:
        format_time = iso_wall_clock if nvr_wall_clock else iso_utc
        body = f'''<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">
  <searchID>{search_id}</searchID>
  <trackList><trackID>{track}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>{format_time(start)}</startTime><endTime>{format_time(end)}</endTime></timeSpan></timeSpanList>
  <maxResults>40</maxResults><searchResultPostion>{position}</searchResultPostion>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>'''.encode()
        root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/search", body))
        items = root.findall(".//{*}searchMatchItem")
        for item in items:
            span = item.find(".//{*}timeSpan")
            uri = MODULE._xml_text(item, "playbackURI")
            item_start = MODULE._xml_text(span, "startTime") if span is not None else None
            item_end = MODULE._xml_text(span, "endTime") if span is not None else None
            if uri and item_start and item_end:
                results.append({"start": item_start, "end": item_end, "uri": uri})
        response_status = MODULE._xml_text(root, "responseStatusStrg")
        num = int(MODULE._xml_text(root, "numOfMatches") or len(items))
        position += len(items)
        if not items or response_status != "MORE" or position >= num:
            break
    unique = {(x["start"], x["end"], x["uri"]): x for x in results}
    return sorted(unique.values(), key=lambda x: x["start"])


def stream_index(nvr, credential, item: dict[str, str], clip_start: datetime, clip_end: datetime, nvr_wall_clock: bool):
    uri = item["uri"]
    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<downloadRequest xmlns="http://www.isapi.org/ver20/XMLSchema" version="1.0">
  <playbackURI>{uri.replace('&', '&amp;')}</playbackURI>
</downloadRequest>'''.encode()
    request = Request(
        f"http://{credential.host}/ISAPI/ContentMgmt/download",
        data=body,
        method="POST",
        headers={"Content-Type": "application/xml"},
    )
    response = nvr.opener.open(request, timeout=30)
    container = av.open(response, format="mpeg")
    stream = container.streams.video[0]
    parse_time = parse_wall_clock if nvr_wall_clock else parse_utc
    item_start = parse_time(item["start"])
    first_pts = None
    bins: dict[int, dict[str, int]] = {}
    try:
        for packet in container.demux(stream):
            if packet.pts is None:
                continue
            pts = float(packet.pts * packet.time_base)
            if first_pts is None:
                first_pts = pts
            offset = max(0, int(pts - first_pts))
            when = item_start + timedelta(seconds=offset)
            if when < clip_start:
                continue
            if when >= clip_end:
                break
            entry = bins.setdefault(offset, {"bytes": 0, "packets": 0, "keyframes": 0})
            entry["bytes"] += packet.size
            entry["packets"] += 1
            entry["keyframes"] += int(packet.is_keyframe)
    finally:
        container.close()
        response.close()
    for offset, values in sorted(bins.items()):
        when = item_start + timedelta(seconds=offset)
        yield when, values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorder", required=True)
    parser.add_argument("--track", type=int, required=True)
    parser.add_argument("--start", required=True, help="Asia/Shanghai, YYYY-mm-ddTHH:MM:SS")
    parser.add_argument("--end", required=True, help="Asia/Shanghai, YYYY-mm-ddTHH:MM:SS")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--nvr-wall-clock",
        action="store_true",
        help="send and interpret recorder timestamps as local wall clock",
    )
    args = parser.parse_args()
    start_local = datetime.fromisoformat(args.start).replace(tzinfo=LOCAL_TZ)
    end_local = datetime.fromisoformat(args.end).replace(tzinfo=LOCAL_TZ)
    start = start_local if args.nvr_wall_clock else start_local.astimezone(timezone.utc)
    end = end_local if args.nvr_wall_clock else end_local.astimezone(timezone.utc)
    credentials, source = MODULE.load_credentials(False, "dws")
    credential = credentials[args.recorder]
    nvr = MODULE.HikvisionNvr(args.recorder, credential, timeout=30)
    items = search(nvr, args.track, start, end, args.nvr_wall_clock)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_local", "bytes", "packets", "keyframes"])
        for item in items:
            parse_time = parse_wall_clock if args.nvr_wall_clock else parse_utc
            item_start = parse_time(item["start"])
            item_end = parse_time(item["end"])
            if item_end <= start or item_start >= end:
                continue
            for when, values in stream_index(nvr, credential, item, start, end, args.nvr_wall_clock):
                writer.writerow([
                    when.astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
                    values["bytes"],
                    values["packets"],
                    values["keyframes"],
                ])
                rows += 1
    print(json.dumps({"source": source, "segments": len(items), "rows": rows, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
