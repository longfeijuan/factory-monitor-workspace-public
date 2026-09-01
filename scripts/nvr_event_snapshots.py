#!/usr/bin/env python3
"""Extract accelerated key review frames for door-motion episodes.

The script speaks RTSP directly so it can request accelerated archive playback
and decode the interleaved HEVC RTP stream without changing recorder settings.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import socket
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import av
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", ROOT / "connector" / "gate_nvr_service.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

from nvr_rtsp_scale_probe import RtspClient  # noqa: E402


LOCAL_TZ = timezone(timedelta(hours=8))


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_wall_clock(value: datetime) -> str:
    """Format local wall time for recorders whose API appends Z without UTC conversion."""
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%SZ")


def playback_url(
    recorder: str,
    track: int,
    start: datetime,
    end: datetime,
    credentials,
    nvr_wall_clock: bool,
):
    credential = credentials[recorder]
    nvr = MODULE.HikvisionNvr(recorder, credential, timeout=20)
    format_time = iso_wall_clock if nvr_wall_clock else iso_utc
    uri = None
    for search_start, search_end in (
        (start, end),
        (start - timedelta(minutes=5), end + timedelta(minutes=5)),
        (start - timedelta(hours=1), end + timedelta(hours=1)),
    ):
        body = f"""<CMSearchDescription><searchID>{uuid.uuid4()}</searchID>
<trackList><trackID>{track}</trackID></trackList>
<timeSpanList><timeSpan><startTime>{format_time(search_start)}</startTime><endTime>{format_time(search_end)}</endTime></timeSpan></timeSpanList>
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
    query["starttime"] = format_time(start).replace("-", "").replace(":", "")
    query["endtime"] = format_time(end).replace("-", "").replace(":", "")
    return (
        urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)),
        credential.username,
        credential.password,
    )


def rtp_payload(packet: bytes) -> tuple[int, bytes] | None:
    if len(packet) < 14:
        return None
    offset = 12 + 4 * (packet[0] & 0x0F)
    if packet[0] & 0x10:
        if len(packet) < offset + 4:
            return None
        offset += 4 + 4 * int.from_bytes(packet[offset + 2 : offset + 4], "big")
    if offset >= len(packet):
        return None
    return int.from_bytes(packet[4:8], "big"), packet[offset:]


def append_hevc_payload(payload: bytes, nals: list[bytes], fu: bytes | None) -> bytes | None:
    if len(payload) < 2:
        return fu
    nal_type = (payload[0] >> 1) & 0x3F
    if nal_type == 48:  # aggregation packet
        offset = 2
        while offset + 2 <= len(payload):
            size = int.from_bytes(payload[offset : offset + 2], "big")
            offset += 2
            if offset + size > len(payload):
                break
            nals.append(payload[offset : offset + size])
            offset += size
        return fu
    if nal_type == 49 and len(payload) >= 3:  # fragmentation unit
        fu_header = payload[2]
        start = bool(fu_header & 0x80)
        finish = bool(fu_header & 0x40)
        original_type = fu_header & 0x3F
        if start:
            fu = bytes([(payload[0] & 0x81) | (original_type << 1), payload[1]]) + payload[3:]
        elif fu is not None:
            fu += payload[3:]
        if finish and fu is not None:
            nals.append(fu)
            fu = None
        return fu
    nals.append(payload)
    return fu


def save_frame(frame, path: Path, max_width: int) -> None:
    image: Image.Image = frame.to_image()
    if image.width > max_width:
        height = round(image.height * max_width / image.width)
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)
    # JPEG optimization is disproportionately expensive during multi-day
    # audits and does not improve evidentiary detail.  Avoid it so accelerated
    # playback is limited by the recorder rather than per-frame file encoding.
    image.save(path, format="JPEG", quality=82, optimize=False)


def extract_episode(
    row: dict[str, str],
    credentials,
    output_dir: Path,
    scale: float,
    pre_seconds: float,
    post_seconds: float,
    sample_start: float,
    sample_step: float,
    max_width: int,
    retries: int,
    session_limit: threading.Semaphore,
    search_lock: threading.Lock,
    resume: bool,
    nvr_wall_clock: bool,
    decoder: str,
):
    event = datetime.fromisoformat(row["start_local"]).replace(tzinfo=LOCAL_TZ)
    start = event - timedelta(seconds=pre_seconds)
    end = event + timedelta(seconds=post_seconds)
    track = int(row["channel"]) * 100 + 1
    episode_dir = output_dir / row["episode_id"]
    episode_dir.mkdir(parents=True, exist_ok=True)
    sample_offsets = []
    offset = sample_start
    # Hikvision archive playback commonly includes about four seconds of pre-roll.
    expected_media = pre_seconds + post_seconds + 4
    while offset < expected_media:
        sample_offsets.append(offset)
        offset += sample_step

    existing = sorted(
        episode_dir.glob("s*_*.jpg"),
        key=lambda path: int(path.name.split("_", 1)[0][1:]),
    )
    if resume and len(existing) >= len(sample_offsets):
        restored = []
        for path in existing[: len(sample_offsets)]:
            match = re.search(r"_(\d+(?:\.\d+)?)\.jpg$", path.name)
            restored.append(
                {
                    "episode_id": row["episode_id"],
                    "gate": row["gate"],
                    "recorder": row["recorder"],
                    "channel": int(row["channel"]),
                    "event_local": row["start_local"],
                    "media_offset_seconds": float(match.group(1)) if match else 0.0,
                    "image": str(path),
                }
            )
        return restored, None

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        client = None
        try:
            session_limit.acquire()
            with search_lock:
                url, username, password = playback_url(
                    row["recorder"], track, start, end, credentials, nvr_wall_clock
                )
            client = RtspClient(url, username, password)
            status, _, sdp = client.request("DESCRIBE", url, {"Accept": "application/sdp"})
            if status != 200:
                raise RuntimeError(f"DESCRIBE {status}")
            controls = re.findall(r"^a=control:(.+)$", sdp.decode(errors="replace"), re.MULTILINE)
            control = next((value.strip() for value in controls if value.strip() != "*"), "trackID=1")
            control_url = control if control.startswith("rtsp://") else urljoin(url.rstrip("/") + "/", control)
            status, _, _ = client.request(
                "SETUP", control_url, {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}
            )
            if status != 200:
                raise RuntimeError(f"SETUP {status}")
            play_headers = {"Range": "npt=0.000-", "Scale": f"{scale:.1f}"}
            if scale >= 16:
                play_headers["Frames"] = "intra"
            status, _, _ = client.request("PLAY", url, play_headers)
            if status != 200:
                raise RuntimeError(f"PLAY {status}")

            codec = av.CodecContext.create(decoder, "r")
            current_timestamp: int | None = None
            first_timestamp: int | None = None
            nals: list[bytes] = []
            fu: bytes | None = None
            next_sample = 0
            saved: list[dict[str, str | float]] = []
            deadline = time.monotonic() + max(12, expected_media / max(scale, 1) * 4 + 4)

            def decode_access_unit(timestamp: int | None) -> None:
                nonlocal nals, next_sample
                if not nals or timestamp is None or first_timestamp is None:
                    nals = []
                    return
                media_seconds = ((timestamp - first_timestamp) & 0xFFFFFFFF) / 90000.0
                encoded = b"".join(b"\x00\x00\x00\x01" + nal for nal in nals)
                nals = []
                # Decode every access unit so inter-predicted frames keep valid
                # references.  Some recorder/firmware combinations ignore the
                # accelerated-playback ``Frames: intra`` request; skipping those
                # intermediate units produces grey/corrupt review images.
                try:
                    frames = codec.decode(av.Packet(encoded))
                except Exception:
                    return
                for frame in frames:
                    if next_sample >= len(sample_offsets) or media_seconds + 0.15 < sample_offsets[next_sample]:
                        continue
                    file_name = f"s{next_sample:02d}_{media_seconds:05.1f}.jpg"
                    path = episode_dir / file_name
                    save_frame(frame, path, max_width)
                    saved.append(
                        {
                            "episode_id": row["episode_id"],
                            "gate": row["gate"],
                            "recorder": row["recorder"],
                            "channel": int(row["channel"]),
                            "event_local": row["start_local"],
                            "media_offset_seconds": round(media_seconds, 3),
                            "image": str(path),
                        }
                    )
                    next_sample += 1

            while time.monotonic() < deadline and next_sample < len(sample_offsets):
                try:
                    marker = client.sock.recv(1)
                except socket.timeout:
                    break
                if not marker:
                    break
                if marker != b"$":
                    continue
                channel = client.sock.recv(1)[0]
                length = int.from_bytes(client.sock.recv(2), "big")
                packet = bytearray()
                while len(packet) < length:
                    packet.extend(client.sock.recv(length - len(packet)))
                if channel != 0:
                    continue
                parsed = rtp_payload(bytes(packet))
                if parsed is None:
                    continue
                timestamp, payload = parsed
                if first_timestamp is None:
                    first_timestamp = timestamp
                if current_timestamp is not None and timestamp != current_timestamp:
                    decode_access_unit(current_timestamp)
                current_timestamp = timestamp
                fu = append_hevc_payload(payload, nals, fu)
            decode_access_unit(current_timestamp)
            if not saved:
                raise RuntimeError("no decoded frames")
            client.close()
            session_limit.release()
            return saved, None
        except Exception as error:
            last_error = error
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            session_limit.release()
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    return [], f"{type(last_error).__name__}: {last_error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episodes", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--gate", action="append", help="only review this gate; repeatable")
    parser.add_argument(
        "--episode-id",
        action="append",
        help="only extract this episode id; repeat to select multiple retry windows",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--scale", type=float, default=8)
    parser.add_argument("--pre-seconds", type=float, default=4)
    parser.add_argument("--post-seconds", type=float, default=9)
    parser.add_argument("--sample-start", type=float, default=0)
    parser.add_argument("--sample-step", type=float, default=3)
    parser.add_argument("--max-width", type=int, default=768)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--decoder",
        default="hevc",
        help="PyAV decoder name; macOS may use hevc_videotoolbox for faster review extraction",
    )
    parser.add_argument("--per-recorder-sessions", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--nvr-wall-clock",
        action="store_true",
        help="send local wall-clock values to NVR APIs that label local time with Z",
    )
    args = parser.parse_args()

    with args.episodes.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if args.gate:
        rows = [row for row in rows if row["gate"] in set(args.gate)]
    if args.episode_id:
        rows = [row for row in rows if row["episode_id"] in set(args.episode_id)]
    if args.limit:
        rows = rows[: args.limit]
    credentials, source = MODULE.load_credentials(False, "dws")
    session_limits = {
        recorder: threading.BoundedSemaphore(args.per_recorder_sessions)
        for recorder in {row["recorder"] for row in rows}
    }
    search_locks = {
        recorder: threading.Lock() for recorder in {row["recorder"] for row in rows}
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str | int | float]] = []
    failures: list[dict[str, str]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                extract_episode,
                row,
                credentials,
                args.output_dir,
                args.scale,
                args.pre_seconds,
                args.post_seconds,
                args.sample_start,
                args.sample_step,
                args.max_width,
                args.retries,
                session_limits[row["recorder"]],
                search_locks[row["recorder"]],
                args.resume,
                args.nvr_wall_clock,
                args.decoder,
            ): row
            for row in rows
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            row = futures[future]
            try:
                frames, error = future.result()
                results.extend(frames)
                if error:
                    failures.append({"episode_id": row["episode_id"], "error": error})
                    print(
                        json.dumps(
                            {"episodeFailure": row["episode_id"], "error": error},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            except Exception as error:
                failures.append(
                    {"episode_id": row["episode_id"], "error": f"{type(error).__name__}: {error}"}
                )
                print(
                    json.dumps(
                        {
                            "episodeFailure": row["episode_id"],
                            "error": f"{type(error).__name__}: {error}",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            if completed % 20 == 0 or completed == len(rows):
                print(
                    json.dumps(
                        {
                            "completed": completed,
                            "total": len(rows),
                            "frames": len(results),
                            "failures": len(failures),
                            "elapsedSeconds": round(time.monotonic() - started, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    results.sort(key=lambda row: (str(row["event_local"]), str(row["episode_id"]), float(row["media_offset_seconds"])))
    with (args.output_dir / "snapshots.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "episode_id",
                "gate",
                "recorder",
                "channel",
                "event_local",
                "media_offset_seconds",
                "image",
            ],
        )
        writer.writeheader()
        writer.writerows(results)
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "credentialSource": source,
                "episodes": len(rows),
                "frames": len(results),
                "failures": failures,
                "scale": args.scale,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
