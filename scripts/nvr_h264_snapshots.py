#!/usr/bin/env python3
"""Extract one H.264 playback frame per event from a Hikvision NVR.

This is intentionally read-only.  It uses the recorder's search API to find a
playback URI, then opens the H.264 RTSP stream through PyAV.  It is separate
from the HEVC RTP extractor because some channels (notably passage camera 49)
return H.264 video.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import av


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", ROOT / "connector" / "gate_nvr_service.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

LOCAL_TZ = timezone(timedelta(hours=8))


def wall_clock(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_wall_clock(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y%m%dT%H%M%SZ")


def playback_url(recorder: str, track: int, event: datetime, credentials: dict, preserve_segment: bool = False):
    credential = credentials[recorder]
    nvr = MODULE.HikvisionNvr(recorder, credential, timeout=20)
    start = event.astimezone(LOCAL_TZ)
    end = start + timedelta(minutes=1)
    for search_start, search_end in (
        (start, end),
        (start - timedelta(minutes=5), end + timedelta(minutes=5)),
        (start - timedelta(hours=1), end + timedelta(hours=1)),
    ):
        body = f"""<CMSearchDescription><searchID>{uuid.uuid4()}</searchID>
<trackList><trackID>{track}</trackID></trackList>
<timeSpanList><timeSpan><startTime>{wall_clock(search_start)}</startTime><endTime>{wall_clock(search_end)}</endTime></timeSpan></timeSpanList>
<maxResults>5</maxResults><searchResultPostion>0</searchResultPostion>
<metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>""".encode()
        root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/search", body))
        uri = MODULE._xml_text(root, "playbackURI")
        if uri:
            break
    if not uri:
        raise RuntimeError("recording missing")
    parts = urlsplit(uri)
    query = dict(parse_qsl(parts.query))
    if not preserve_segment:
        query["starttime"] = compact_wall_clock(start - timedelta(minutes=1))
        query["endtime"] = compact_wall_clock(end + timedelta(minutes=1))
    # PyAV handles RTSP Digest authentication when credentials are in the URL.
    auth_url = urlunsplit(
        (parts.scheme, f"{credential.username}:{credential.password}@{parts.hostname}", parts.path, urlencode(query), parts.fragment)
    )
    return auth_url


def extract(
    row: dict[str, str],
    credentials: dict,
    output_dir: Path,
    resume: bool,
    preserve_segment: bool,
    event_shift_seconds: float,
) -> dict[str, str]:
    event = datetime.fromisoformat(row["start_local"]).replace(tzinfo=LOCAL_TZ) + timedelta(seconds=event_shift_seconds)
    episode_dir = output_dir / row["episode_id"]
    episode_dir.mkdir(parents=True, exist_ok=True)
    output = episode_dir / "frame.jpg"
    if resume and output.exists():
        return {**row, "status": "ok", "image": str(output)}
    track = int(row["channel"]) * 100 + 1
    stream_url = playback_url(row["recorder"], track, event, credentials, preserve_segment)
    container = av.open(stream_url, options={"rtsp_transport": "tcp", "stimeout": "10000000"})
    try:
        if not container.streams.video:
            raise RuntimeError("no video stream")
        frame = next(container.decode(video=0), None)
        if frame is None:
            raise RuntimeError("no decoded frames")
        frame.to_image().save(output, format="JPEG", quality=88)
    finally:
        container.close()
    return {**row, "status": "ok", "image": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episodes")
    parser.add_argument("output_dir")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--preserve-segment-uri", action="store_true")
    parser.add_argument(
        "--import-from-dingtalk",
        action="store_true",
        help="import NVR credentials from DingTalk into this process only",
    )
    parser.add_argument(
        "--event-shift-seconds",
        type=float,
        default=0.0,
        help="shift each requested event before opening playback; use 60 to cancel the helper pre-roll",
    )
    args = parser.parse_args()
    credentials, _ = MODULE.load_credentials(args.import_from_dingtalk, "dws")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with Path(args.episodes).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if args.limit:
        rows = rows[: args.limit]
    results: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                extract,
                row,
                credentials,
                output_dir,
                args.resume,
                args.preserve_segment_uri,
                args.event_shift_seconds,
            ): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                failures.append({"episode_id": row["episode_id"], "error": f"{type(error).__name__}: {error}"})
    results.sort(key=lambda row: row["episode_id"])
    with (output_dir / "snapshots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["episode_id", "gate", "recorder", "channel", "start_local", "end_local", "status", "image"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)
    (output_dir / "summary.json").write_text(
        __import__("json").dumps({"episodes": len(rows), "frames": len(results), "failures": failures}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print({"episodes": len(rows), "frames": len(results), "failures": len(failures)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
