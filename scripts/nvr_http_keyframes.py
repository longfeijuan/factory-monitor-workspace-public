#!/usr/bin/env python3
"""Read-only full-archive keyframe sampler through Hikvision HTTP download."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request

import av
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nvr_motion_index", ROOT / "scripts" / "nvr_motion_index.py"
)
assert SPEC and SPEC.loader
INDEX = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = INDEX
SPEC.loader.exec_module(INDEX)
LOCAL_TZ = timezone(timedelta(hours=8))


def save(frame, path: Path, max_width: int, quality: int) -> None:
    image: Image.Image = frame.to_image()
    if image.width > max_width:
        image = image.resize(
            (max_width, round(image.height * max_width / image.width)),
            Image.Resampling.BILINEAR,
        )
    image.save(path, "JPEG", quality=quality, optimize=False)


def sample_item(
    nvr,
    credential,
    item: dict[str, str],
    clip_start: datetime,
    clip_end: datetime,
    nvr_wall_clock: bool,
    output_dir: Path,
    max_width: int,
    quality: int,
    min_step: float,
):
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
    stream.codec_context.skip_frame = "NONKEY"
    parse_time = INDEX.parse_wall_clock if nvr_wall_clock else INDEX.parse_utc
    item_start = parse_time(item["start"])
    first_pts: float | None = None
    last_saved: datetime | None = None
    rows = []
    try:
        for frame in container.decode(stream):
            pts = float(frame.time or 0.0)
            if first_pts is None:
                first_pts = pts
            when = item_start + timedelta(seconds=max(0.0, pts - first_pts))
            if when < clip_start:
                continue
            if when >= clip_end:
                break
            if last_saved is not None and (when - last_saved).total_seconds() < min_step:
                continue
            local = when.astimezone(LOCAL_TZ)
            name = local.strftime("k%Y%m%dT%H%M%S") + f"_{len(rows):06d}.jpg"
            path = output_dir / name
            save(frame, path, max_width, quality)
            rows.append(
                {
                    "timestamp_local": local.isoformat(timespec="milliseconds"),
                    "image": str(path),
                    "key_frame": int(bool(frame.key_frame)),
                }
            )
            last_saved = when
    finally:
        container.close()
        response.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorder", required=True)
    parser.add_argument("--track", type=int, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-width", type=int, default=320)
    parser.add_argument("--quality", type=int, default=75)
    parser.add_argument("--min-step", type=float, default=2.0)
    parser.add_argument("--nvr-wall-clock", action="store_true")
    args = parser.parse_args()

    start_local = datetime.fromisoformat(args.start).replace(tzinfo=LOCAL_TZ)
    end_local = datetime.fromisoformat(args.end).replace(tzinfo=LOCAL_TZ)
    start = start_local if args.nvr_wall_clock else start_local.astimezone(timezone.utc)
    end = end_local if args.nvr_wall_clock else end_local.astimezone(timezone.utc)
    credentials, source = INDEX.MODULE.load_credentials(False, "dws")
    credential = credentials[args.recorder]
    nvr = INDEX.MODULE.HikvisionNvr(args.recorder, credential, timeout=30)
    items = INDEX.search(nvr, args.track, start, end, args.nvr_wall_clock)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in items:
        parse_time = INDEX.parse_wall_clock if args.nvr_wall_clock else INDEX.parse_utc
        item_start = parse_time(item["start"])
        item_end = parse_time(item["end"])
        if item_end <= start or item_start >= end:
            continue
        rows.extend(
            sample_item(
                nvr,
                credential,
                item,
                start,
                end,
                args.nvr_wall_clock,
                args.output_dir,
                args.max_width,
                args.quality,
                args.min_step,
            )
        )
    rows.sort(key=lambda row: row["timestamp_local"])
    with (args.output_dir / "snapshots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp_local", "image", "key_frame"])
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "source": source,
        "segments": len(items),
        "frames": len(rows),
        "startLocal": args.start,
        "endLocal": args.end,
        "minStep": args.min_step,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
