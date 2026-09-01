#!/usr/bin/env python3
"""Sample decoded H.264 archive frames while Hikvision fast playback is active.

The recorder honours an RTSP ``Scale`` header, but common RTSP clients do not
expose that header reliably.  This read-only helper uses the small RTSP client
from ``nvr_rtsp_scale_probe.py``, depacketizes interleaved RTP/H.264, and saves
one frame per requested amount of media time.
"""

from __future__ import annotations

import argparse
import base64
import csv
import importlib.util
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import av
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nvr_rtsp_scale_probe", ROOT / "scripts" / "nvr_rtsp_scale_probe.py"
)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROBE
SPEC.loader.exec_module(PROBE)
LOCAL = timezone(timedelta(hours=8))
START_CODE = b"\x00\x00\x00\x01"


def rtp_payload(packet: bytes) -> tuple[int, bool, bytes] | None:
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return None
    padding = bool(packet[0] & 0x20)
    extension = bool(packet[0] & 0x10)
    cc = packet[0] & 0x0F
    marker = bool(packet[1] & 0x80)
    timestamp = int.from_bytes(packet[4:8], "big")
    offset = 12 + cc * 4
    if extension:
        if len(packet) < offset + 4:
            return None
        words = int.from_bytes(packet[offset + 2 : offset + 4], "big")
        offset += 4 + words * 4
    end = len(packet)
    if padding and packet:
        end -= packet[-1]
    if offset >= end:
        return None
    return timestamp, marker, packet[offset:end]


def append_h264(payload: bytes, access_unit: bytearray, fu: bytearray | None):
    nal_type = payload[0] & 0x1F
    if 1 <= nal_type <= 23:
        access_unit.extend(START_CODE)
        access_unit.extend(payload)
        return None
    if nal_type == 24:  # STAP-A
        cursor = 1
        while cursor + 2 <= len(payload):
            size = int.from_bytes(payload[cursor : cursor + 2], "big")
            cursor += 2
            if size <= 0 or cursor + size > len(payload):
                break
            access_unit.extend(START_CODE)
            access_unit.extend(payload[cursor : cursor + size])
            cursor += size
        return None
    if nal_type != 28 or len(payload) < 2:  # FU-A is the only fragmentation used here
        return fu
    indicator, header = payload[0], payload[1]
    start, end = bool(header & 0x80), bool(header & 0x40)
    if start:
        fu = bytearray(START_CODE)
        fu.append((indicator & 0xE0) | (header & 0x1F))
        fu.extend(payload[2:])
    elif fu is not None:
        fu.extend(payload[2:])
    if end and fu is not None:
        access_unit.extend(fu)
        fu = None
    return fu


def append_hevc(payload: bytes, access_unit: bytearray, fu: bytearray | None):
    if len(payload) < 2:
        return fu
    nal_type = (payload[0] >> 1) & 0x3F
    if nal_type < 48:
        access_unit.extend(START_CODE)
        access_unit.extend(payload)
        return None
    if nal_type == 48:  # Aggregation packet
        cursor = 2
        while cursor + 2 <= len(payload):
            size = int.from_bytes(payload[cursor : cursor + 2], "big")
            cursor += 2
            if size <= 0 or cursor + size > len(payload):
                break
            access_unit.extend(START_CODE)
            access_unit.extend(payload[cursor : cursor + size])
            cursor += size
        return None
    if nal_type != 49 or len(payload) < 3:  # Fragmentation unit
        return fu
    header = payload[2]
    start, end, original_type = bool(header & 0x80), bool(header & 0x40), header & 0x3F
    if start:
        fu = bytearray(START_CODE)
        fu.append((payload[0] & 0x81) | (original_type << 1))
        fu.append(payload[1])
        fu.extend(payload[3:])
    elif fu is not None:
        fu.extend(payload[3:])
    if end and fu is not None:
        access_unit.extend(fu)
        fu = None
    return fu


def save_frame(frame: av.VideoFrame, path: Path, max_width: int) -> None:
    image: Image.Image = frame.to_image()
    if max_width and image.width > max_width:
        image = image.resize(
            (max_width, round(image.height * max_width / image.width)),
            Image.Resampling.LANCZOS,
        )
    image.save(path, "JPEG", quality=86, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorder", default="nvr-main-02")
    parser.add_argument("--track", type=int, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--scale", type=float, default=32.0)
    parser.add_argument("--step", type=float, default=30.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-width", type=int, default=1280)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=LOCAL)
    end = datetime.fromisoformat(args.end).replace(tzinfo=LOCAL)
    if end <= start:
        parser.error("--end must be after --start")

    # Reuse the proven playback URL builder by setting the same environment it reads.
    import os

    os.environ["NVR_RECORDER"] = args.recorder
    os.environ["NVR_TRACK"] = str(args.track)
    os.environ["NVR_START"] = args.start
    os.environ["NVR_END"] = args.end
    os.environ["NVR_WALL_CLOCK"] = "1"
    url, username, password = PROBE.playback_url()
    client = PROBE.RtspClient(url, username, password)
    status, _, sdp = client.request("DESCRIBE", url, {"Accept": "application/sdp"})
    if status != 200:
        raise RuntimeError(f"DESCRIBE failed: {status}")
    text = sdp.decode(errors="replace")
    controls = re.findall(r"^a=control:(.+)$", text, re.MULTILINE)
    control = next((value.strip() for value in controls if value.strip() != "*"), "trackID=1")
    control_url = control if control.startswith("rtsp://") else urljoin(url.rstrip("/") + "/", control)
    status, _, _ = client.request(
        "SETUP", control_url, {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}
    )
    if status != 200:
        raise RuntimeError(f"SETUP failed: {status}")
    play_headers = {"Range": "npt=0.000-", "Scale": str(args.scale)}
    if args.scale >= 16:
        play_headers["Frames"] = "intra"
    status, _, _ = client.request("PLAY", url, play_headers)
    if status != 200:
        raise RuntimeError(f"PLAY failed: {status}")

    is_hevc = bool(re.search(r"a=rtpmap:\d+ H265/", text, re.IGNORECASE))
    parameter_sets = bytearray()
    match = re.search(r"sprop-parameter-sets=([^;\r\n]+)", text)
    if match:
        for encoded in match.group(1).split(","):
            parameter_sets.extend(START_CODE)
            parameter_sets.extend(base64.b64decode(encoded))

    args.out.mkdir(parents=True, exist_ok=True)
    codec = av.CodecContext.create("hevc" if is_hevc else "h264", "r")
    rows: list[dict[str, str | float]] = []
    first_timestamp: int | None = None
    current_timestamp: int | None = None
    access_unit = bytearray(parameter_sets)
    fu: bytearray | None = None
    next_save = 0.0

    def decode_current(timestamp: int | None) -> None:
        nonlocal access_unit, next_save
        if timestamp is None or not access_unit:
            return
        media = ((timestamp - first_timestamp) & 0xFFFFFFFF) / 90000.0  # type: ignore[arg-type]
        try:
            decoded = codec.decode(av.Packet(bytes(access_unit)))
        except Exception:
            decoded = []
        for frame in decoded:
            if media + 0.2 < next_save:
                continue
            when = start + timedelta(seconds=media)
            path = args.out / f"{when:%Y%m%d-%H%M%S}.jpg"
            save_frame(frame, path, args.max_width)
            rows.append(
                {
                    "timestamp_local": when.isoformat(timespec="seconds"),
                    "media_seconds": round(media, 3),
                    "image": str(path),
                }
            )
            while next_save <= media + 0.2:
                next_save += args.step
            break
        access_unit = bytearray()

    try:
        while True:
            marker = client.sock.recv(1)
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
            if not parsed:
                continue
            timestamp, marked, payload = parsed
            if first_timestamp is None:
                first_timestamp = timestamp
            media = ((timestamp - first_timestamp) & 0xFFFFFFFF) / 90000.0
            if media >= (end - start).total_seconds():
                decode_current(current_timestamp)
                break
            if current_timestamp is not None and timestamp != current_timestamp:
                decode_current(current_timestamp)
                access_unit = bytearray()
                fu = None
            current_timestamp = timestamp
            fu = (append_hevc if is_hevc else append_h264)(payload, access_unit, fu)
            if marked:
                decode_current(current_timestamp)
                current_timestamp = None
                fu = None
    finally:
        try:
            client.request("TEARDOWN", url)
        except Exception:
            pass
        client.close()

    with (args.out / "frames.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp_local", "media_seconds", "image"])
        writer.writeheader()
        writer.writerows(rows)
    print({"frames": len(rows), "out": str(args.out), "scale": args.scale})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
