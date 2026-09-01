#!/usr/bin/env python3
"""Read-only availability scan for Hikvision archive playback.

Classifies each probe using the same RTSP path used for visual review:
``available`` (DESCRIBE 200), ``playback_error`` (a recording URI exists but
the recorder rejects playback), or ``recording_missing`` (no URI returned).
The results are operational diagnostics, not evidence of personnel activity.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import nvr_event_snapshots as snapshots  # noqa: E402
from nvr_rtsp_scale_probe import RtspClient  # noqa: E402


LOCAL_TZ = timezone(timedelta(hours=8))


def probe(when: datetime, minutes: int, credentials, rtsp_timeout: float, recorder: str, track: int) -> dict[str, str | int]:
    end = when + timedelta(minutes=minutes)
    result: dict[str, str | int] = {
        "probe_local": when.isoformat(timespec="minutes"),
        "probe_end_local": end.isoformat(timespec="minutes"),
        "status": "unknown",
        "detail": "",
    }
    client = None
    try:
        url, username, password = snapshots.playback_url(
            recorder, track, when, end, credentials, True
        )
        client = RtspClient(url, username, password)
        client.sock.settimeout(rtsp_timeout)
        code, _, _ = client.request("DESCRIBE", url, {"Accept": "application/sdp"})
        result["status"] = "available" if code == 200 else "playback_error"
        result["detail"] = f"RTSP_DESCRIBE_{code}"
    except RuntimeError as error:
        text = str(error)
        result["status"] = "recording_missing" if text == "recording missing" else "probe_error"
        result["detail"] = text
    except Exception as error:  # Keep transient failures visible; do not treat as missing footage.
        result["status"] = "probe_error"
        result["detail"] = f"{type(error).__name__}: {error}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return result


def status_intervals(rows: list[dict[str, str | int]], end: datetime) -> list[dict[str, str]]:
    if not rows:
        return []
    intervals: list[dict[str, str]] = []
    first = rows[0]
    active_status = str(first["status"])
    active_start = str(first["probe_local"])
    active_detail = str(first["detail"])
    for row in rows[1:]:
        if str(row["status"]) != active_status:
            intervals.append(
                {"start_local": active_start, "end_local": str(row["probe_local"]), "status": active_status, "detail": active_detail}
            )
            active_status = str(row["status"])
            active_start = str(row["probe_local"])
            active_detail = str(row["detail"])
    intervals.append(
        {"start_local": active_start, "end_local": end.isoformat(timespec="minutes"), "status": active_status, "detail": active_detail}
    )
    return intervals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="Asia/Shanghai local time")
    parser.add_argument("--end", required=True, help="Asia/Shanghai local time")
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--probe-minutes", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rtsp-timeout", type=float, default=3.0)
    parser.add_argument("--recorder", default="nvr-main-02")
    parser.add_argument("--track", type=int, default=101)
    parser.add_argument("--camera", default="58号大门")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.interval_minutes <= 0 or args.probe_minutes <= 0 or args.workers <= 0 or args.rtsp_timeout <= 0:
        parser.error("interval, probe, and workers must be positive")
    start = datetime.fromisoformat(args.start).replace(tzinfo=LOCAL_TZ)
    end = datetime.fromisoformat(args.end).replace(tzinfo=LOCAL_TZ)
    if end <= start:
        parser.error("end must be after start")
    credentials, source = snapshots.MODULE.load_credentials(False, "dws")
    probes = []
    cursor = start
    while cursor < end:
        probes.append(cursor)
        cursor += timedelta(minutes=args.interval_minutes)
    rows: list[dict[str, str | int]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(probe, when, args.probe_minutes, credentials, args.rtsp_timeout, args.recorder, args.track)
            for when in probes
        ]
        for completed, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if completed % 100 == 0 or completed == len(futures):
                print(json.dumps({"completed": completed, "total": len(futures)}, ensure_ascii=False), flush=True)
    rows.sort(key=lambda row: str(row["probe_local"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["probe_local", "probe_end_local", "status", "detail"])
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "recorder": args.recorder,
        "channel": args.track // 100,
        "camera": args.camera,
        "credentialSource": source,
        "startLocal": start.isoformat(timespec="minutes"),
        "endLocal": end.isoformat(timespec="minutes"),
        "intervalMinutes": args.interval_minutes,
        "probeMinutes": args.probe_minutes,
        "counts": {status: sum(1 for row in rows if row["status"] == status) for status in sorted({str(row["status"]) for row in rows})},
        "intervals": status_intervals(rows, end),
    }
    args.output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
