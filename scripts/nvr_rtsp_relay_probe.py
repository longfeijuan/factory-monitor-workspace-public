#!/usr/bin/env python3
"""Relay fast archived RTP to localhost and decode it with PyAV."""

from __future__ import annotations

import os
import re
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import av

from nvr_rtsp_scale_probe import RtspClient, playback_url


def main() -> int:
    scale = os.environ.get("NVR_SCALE", "32.0")
    url, username, password = playback_url()
    client = RtspClient(url, username, password)
    status, _, sdp = client.request("DESCRIBE", url, {"Accept": "application/sdp"})
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

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()

    video_section = re.search(r"(?ms)^m=video .*?(?=^m=|\Z)", sdp_text)
    if not video_section:
        raise RuntimeError("video section missing from SDP")
    section = video_section.group(0)
    section = re.sub(r"^m=video \d+", f"m=video {port}", section, flags=re.MULTILINE)
    section = re.sub(r"^a=control:.*$", "", section, flags=re.MULTILINE)
    relay_sdp = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=DoorAudit\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n" + section.replace("\n", "\r\n")
    temp = tempfile.NamedTemporaryFile("w", suffix=".sdp", delete=False)
    temp.write(relay_sdp)
    temp.close()

    stop = threading.Event()
    packet_count = 0

    def relay() -> None:
        nonlocal packet_count
        try:
            while not stop.is_set():
                marker = client.sock.recv(1)
                if not marker:
                    return
                if marker != b"$":
                    continue
                channel = client.sock.recv(1)[0]
                length = int.from_bytes(client.sock.recv(2), "big")
                payload = bytearray()
                while len(payload) < length:
                    payload.extend(client.sock.recv(length - len(payload)))
                if channel == 0:
                    packet_count += 1
                    udp.sendto(payload, ("127.0.0.1", port))
        except OSError:
            return

    status, _, _ = client.request("PLAY", url, {"Range": "npt=0.000-", "Scale": scale})
    if status != 200:
        raise RuntimeError(f"PLAY failed: {status}")
    thread = threading.Thread(target=relay, daemon=True)
    thread.start()
    started = time.monotonic()
    frames = 0
    first_time = None
    last_time = None
    try:
        container = av.open(
            temp.name,
            format="sdp",
            options={"protocol_whitelist": "file,udp,rtp", "stimeout": "10000000"},
            timeout=12,
        )
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            frames += 1
            first_time = frame.time if first_time is None else first_time
            last_time = frame.time
            if first_time is not None and last_time is not None and last_time - first_time >= 300:
                break
        container.close()
    finally:
        stop.set()
        client.close()
        udp.close()
        Path(temp.name).unlink(missing_ok=True)
    print(
        {
            "scale": scale,
            "wall_seconds": round(time.monotonic() - started, 2),
            "media_seconds": round((last_time or 0) - (first_time or 0), 2),
            "decoded_frames": frames,
            "rtp_packets": packet_count,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
