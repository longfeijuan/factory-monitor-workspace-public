#!/usr/bin/env python3
"""Read Hikvision motion-start logs and keep events for selected channels.

The recorder web UI treats the timestamps in its log search form as local wall
time even though it appends ``Z``.  This script deliberately mirrors that
behaviour so the returned timestamps line up with the recorder OSD and UI.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "connector" / "gate_nvr_service.py"
SPEC = importlib.util.spec_from_file_location("gate_nvr_service", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


TARGETS = {
    "nvr-main-01": {41: "后门", 64: "后门区右"},
    "nvr-main-02": {1: "前门", 5: "侧门"},
}


@dataclass(frozen=True)
class Window:
    recorder: str
    start: datetime
    end: datetime


def fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def xml_text(element: ET.Element, name: str) -> str:
    found = element.find(f".//{{*}}{name}")
    return found.text.strip() if found is not None and found.text else ""


def search_window(window: Window, credentials, targets, meta_id: str, timeout: float, retries: int):
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            nvr = MODULE.HikvisionNvr(window.recorder, credentials[window.recorder], timeout=timeout)
            search_id = str(uuid.uuid4())
            position = 0
            pages = 0
            total = 0
            kept: list[dict[str, str | int]] = []
            while True:
                position_xml = (
                    f"<searchResultPostion>{position}</searchResultPostion>" if position else ""
                )
                body = f"""<?xml version='1.0' encoding='utf-8'?>
<CMSearchDescription><searchID>{search_id}</searchID>
<metaId>{meta_id}</metaId>
<timeSpanList><timeSpan><startTime>{fmt(window.start)}</startTime><endTime>{fmt(window.end)}</endTime></timeSpan></timeSpanList>
<maxResults>64</maxResults>{position_xml}</CMSearchDescription>""".encode()
                root = ET.fromstring(nvr.request("/ISAPI/ContentMgmt/logSearch", body))
                items = root.findall(".//{*}searchMatchItem")
                pages += 1
                total += len(items)
                for item in items:
                    local_id = xml_text(item, "localId")
                    if not local_id.startswith("D"):
                        continue
                    try:
                        channel = int(local_id[1:])
                    except ValueError:
                        continue
                    if channel not in targets[window.recorder]:
                        continue
                    kept.append(
                        {
                            "recorder": window.recorder,
                            "channel": channel,
                            "gate": targets[window.recorder][channel],
                            "timestamp_local": xml_text(item, "StartDateTime").removesuffix("Z"),
                            "meta_id": xml_text(item, "metaId"),
                        }
                    )
                status = xml_text(root, "responseStatusStrg")
                position += len(items)
                if status != "MORE" or not items:
                    return kept, {
                        "recorder": window.recorder,
                        "start": fmt(window.start),
                        "end": fmt(window.end),
                        "pages": pages,
                        "logsScanned": total,
                        "targetEvents": len(kept),
                        "status": status,
                    }
                if pages > 500:
                    raise RuntimeError(f"pagination runaway for {window}")
        except Exception as error:  # retry individual read-only windows
            last_error = error
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="local wall time, YYYY-mm-ddTHH:MM:SS")
    parser.add_argument("--end", required=True, help="local wall time, YYYY-mm-ddTHH:MM:SS")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--meta-id",
        default="log.std-cgi.com/Alarm/motionStart",
        help="recorder log metaId to query",
    )
    parser.add_argument(
        "--target",
        action="append",
        help="limit search to recorder:channel:label; repeat for multiple targets",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    if end <= start:
        parser.error("--end must be after --start")
    credentials, source = MODULE.load_credentials(False, "dws")
    targets = TARGETS
    if args.target:
        targets = {}
        for raw in args.target:
            try:
                recorder, channel_text, label = raw.split(":", 2)
                channel = int(channel_text)
            except ValueError:
                parser.error("--target must use recorder:channel:label")
            if recorder not in credentials or channel <= 0 or not label:
                parser.error(f"invalid --target: {raw}")
            targets.setdefault(recorder, {})[channel] = label

    windows: list[Window] = []
    for recorder in targets:
        cursor = start
        while cursor < end:
            window_end = min(end, cursor + timedelta(minutes=args.window_minutes))
            windows.append(Window(recorder, cursor, window_end))
            cursor = window_end

    output_lock = threading.Lock()
    all_events: list[dict[str, str | int]] = []
    all_stats: list[dict[str, str | int]] = []
    failures: list[dict[str, str]] = []
    completed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                search_window,
                window,
                credentials,
                targets,
                args.meta_id,
                args.timeout,
                args.retries,
            ): window
            for window in windows
        }
        for future in as_completed(futures):
            window = futures[future]
            try:
                events, stats = future.result()
                with output_lock:
                    all_events.extend(events)
                    all_stats.append(stats)
            except Exception as error:
                failures.append(
                    {
                        "recorder": window.recorder,
                        "start": fmt(window.start),
                        "end": fmt(window.end),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            completed += 1
            if completed % 20 == 0 or completed == len(windows):
                elapsed = time.time() - started
                print(
                    json.dumps(
                        {
                            "completed": completed,
                            "total": len(windows),
                            "events": len(all_events),
                            "failures": len(failures),
                            "elapsedSeconds": round(elapsed, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    all_events.sort(key=lambda row: (str(row["timestamp_local"]), str(row["recorder"]), int(row["channel"])))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp_local", "gate", "recorder", "channel", "meta_id"],
        )
        writer.writeheader()
        writer.writerows(all_events)

    summary = {
        "credentialSource": source,
        "startLocal": args.start,
        "endLocal": args.end,
        "windows": len(windows),
        "windowsCompleted": len(all_stats),
        "failures": failures,
        "logsScanned": sum(int(row["logsScanned"]) for row in all_stats),
        "pages": sum(int(row["pages"]) for row in all_stats),
        "targetEvents": len(all_events),
        "byGate": {
            gate: sum(1 for row in all_events if row["gate"] == gate)
            for gate in sorted({label for channels in targets.values() for label in channels.values()})
        },
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
