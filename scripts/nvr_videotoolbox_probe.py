#!/usr/bin/env python3
"""Benchmark native VideoToolbox decoding of a Hikvision archive stream."""

from __future__ import annotations

import importlib.util
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request

import av
import CoreMedia as CM
import Quartz
import VideoToolbox as VT


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", ROOT / "connector" / "gate_nvr_service.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


START_CODE = re.compile(b"\x00\x00\x00?\x01")


def parameter_sets(extradata: bytes) -> tuple[bytes, ...]:
    return tuple(part for part in START_CODE.split(extradata) if part)


def length_prefixed(payload: bytes) -> bytes:
    parts = [part for part in START_CODE.split(payload) if part]
    if not parts:
        return payload
    return b"".join(len(part).to_bytes(4, "big") + part for part in parts)


def main() -> int:
    credentials, _ = MODULE.load_credentials(False, "dws")
    credential = credentials["nvr-main-02"]
    nvr = MODULE.HikvisionNvr("nvr-main-02", credential, timeout=20)
    body = f'''<CMSearchDescription><searchID>{uuid.uuid4()}</searchID><trackList><trackID>501</trackID></trackList><timeSpanList><timeSpan><startTime>2026-08-01T00:00:00Z</startTime><endTime>2026-08-01T00:10:00Z</endTime></timeSpan></timeSpanList><maxResults>5</maxResults><searchResultPostion>0</searchResultPostion><metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList></CMSearchDescription>'''.encode()
    root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/search", body))
    uri = MODULE._xml_text(root, "playbackURI")
    if not uri:
        raise RuntimeError("recording missing")
    download = f'''<downloadRequest><playbackURI>{uri.replace('&', '&amp;')}</playbackURI></downloadRequest>'''.encode()
    request = Request(
        f"http://{credential.host}/ISAPI/ContentMgmt/download",
        data=download,
        method="POST",
        headers={"Content-Type": "application/xml"},
    )
    response = nvr.opener.open(request, timeout=30)
    container = av.open(response, format="mpeg")
    stream = container.streams.video[0]
    if stream.codec_context.name != "hevc":
        raise RuntimeError(stream.codec_context.name)
    sets = parameter_sets(stream.codec_context.extradata)
    status, format_description = CM.CMVideoFormatDescriptionCreateFromHEVCParameterSets(
        None, len(sets), sets, tuple(map(len, sets)), 4, None, None
    )
    if status != 0:
        raise RuntimeError(f"format description: {status}")
    status, session = VT.VTDecompressionSessionCreate(
        None,
        format_description,
        {VT.kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder: True},
        {Quartz.kCVPixelBufferPixelFormatTypeKey: Quartz.kCVPixelFormatType_32BGRA},
        None,
        None,
    )
    if status != 0:
        raise RuntimeError(f"session: {status}")
    decoded = 0
    callback_errors: list[tuple[int, int]] = []

    def output_handler(status, info_flags, image_buffer, presentation_time, duration):
        nonlocal decoded
        if status == 0 and image_buffer is not None:
            decoded += 1
        else:
            callback_errors.append((status, info_flags))

    first_pts = None
    last_pts = None
    packets = 0
    started = time.monotonic()
    try:
        for packet in container.demux(stream):
            if packet.pts is None:
                continue
            pts = float(packet.pts * packet.time_base)
            first_pts = pts if first_pts is None else first_pts
            last_pts = pts
            encoded = length_prefixed(bytes(packet))
            status, block = CM.CMBlockBufferCreateWithMemoryBlock(
                None, None, len(encoded), None, None, 0, len(encoded), 0, None
            )
            if status != 0:
                raise RuntimeError(f"block: {status}")
            status = CM.CMBlockBufferReplaceDataBytes(encoded, block, 0, len(encoded))
            if status != 0:
                raise RuntimeError(f"block copy: {status}")
            status, sample = CM.CMSampleBufferCreateReady(
                None, block, format_description, 1, 0, (), 1, (len(encoded),), None
            )
            if status != 0:
                raise RuntimeError(f"sample: {status}")
            status, _ = VT.VTDecompressionSessionDecodeFrameWithOutputHandler(
                session, sample, 0, None, output_handler
            )
            if status != 0:
                raise RuntimeError(f"decode: {status}")
            packets += 1
            if first_pts is not None and last_pts - first_pts >= 60:
                break
        VT.VTDecompressionSessionWaitForAsynchronousFrames(session)
    finally:
        VT.VTDecompressionSessionInvalidate(session)
        container.close()
        response.close()
    print(
        {
            "wall_seconds": round(time.monotonic() - started, 2),
            "media_seconds": round((last_pts or 0) - (first_pts or 0), 2),
            "packets": packets,
            "decoded": decoded,
            "callback_errors": callback_errors[:5],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
