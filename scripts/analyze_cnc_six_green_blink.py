#!/usr/bin/env python3
"""Audit six first-floor CNC green tower lights over short video windows.

The lamps blink during normal operation, so a single dark frame is not enough
to call a machine stopped.  Each five-minute sample is decoded as a continuous
window and classified from the sequence of lamp states.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import av


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NVR = load_module("nvr_h264_snapshots_blink", ROOT / "scripts" / "nvr_h264_snapshots.py")
GREEN = load_module("analyze_cnc_six_green_base", ROOT / "scripts" / "analyze_cnc_six_green.py")


def redact_error(value: str) -> str:
    """Remove RTSP credentials before errors are logged or written to reports."""
    return re.sub(r"(rtsp://)[^/@\s]+@", r"\1***:***@", value)


def shift_fields(value: datetime) -> tuple[str, str]:
    if 8 <= value.hour < 20:
        return value.date().isoformat(), "白班"
    shift_date = value.date() if value.hour >= 20 else (value - timedelta(days=1)).date()
    return shift_date.isoformat(), "夜班"


def checkpoint_path(output: Path, episode_id: str) -> Path:
    return output / "checkpoints" / f"{episode_id}.json"


def decode_episode(
    row: dict[str, str],
    credentials: dict,
    output: Path,
    seconds: float,
    step: float,
    minimum_span: float,
    event_shift_seconds: float,
    threshold: int,
    dominance_threshold: float,
    minimum_green_frames: int,
    strong_single_frame_green_pixels: int,
    ambiguous_review_seconds: float,
    ambiguous_review_minimum_span: float,
    resume: bool,
) -> dict:
    checkpoint = checkpoint_path(output, row["episode_id"])
    if resume and checkpoint.exists():
        return json.loads(checkpoint.read_text(encoding="utf-8"))

    event = datetime.fromisoformat(row["start_local"]).replace(tzinfo=NVR.LOCAL_TZ)
    event += timedelta(seconds=event_shift_seconds)
    stream_url = NVR.playback_url(
        row["recorder"], int(row["channel"]) * 100 + 1, event, credentials
    )
    container = av.open(
        stream_url,
        options={"rtsp_transport": "tcp", "stimeout": "15000000"},
    )
    observations = {machine: [] for machine in GREEN.MACHINE_ROIS}
    offsets: list[float] = []
    decoded_sizes: set[tuple[int, int]] = set()
    review_needed: bool | None = None
    try:
        if not container.streams.video:
            raise RuntimeError("no video stream")
        next_offset = 0.0
        for frame in container.decode(video=0):
            offset = float(frame.time or 0.0)
            if offset > seconds + 1e-3 and review_needed is None:
                base_span = offsets[-1] - offsets[0] if len(offsets) >= 2 else 0.0
                base_minimum_frames = max(2, math.floor(minimum_span / step) + 1)
                base_complete = (
                    base_span + 1e-3 >= minimum_span
                    and len(offsets) >= base_minimum_frames
                )
                review_needed = not base_complete or any(
                    sum(value["green"] for value in values) == 1
                    for values in observations.values()
                )
            if offset > ambiguous_review_seconds + 1e-3:
                break
            if offset > seconds + 1e-3 and not review_needed:
                break
            if offsets and offset + 1e-3 < next_offset:
                continue
            image = frame.to_image().convert("RGB")
            decoded_sizes.add(image.size)
            offsets.append(offset)
            next_offset = offset + step
            for machine, reference_roi in GREEN.MACHINE_ROIS.items():
                roi = GREEN.scaled_roi(reference_roi, image.size)
                pixels, dominance, maximum = GREEN.green_metrics(image, roi)
                observations[machine].append(
                    {
                        "offset": round(offset, 3),
                        "green_pixels": pixels,
                        "dominance_p995": round(dominance, 2),
                        "green_max": maximum,
                        "green": int(
                            pixels >= threshold and dominance >= dominance_threshold
                        ),
                    }
                )
    finally:
        container.close()

    span = offsets[-1] - offsets[0] if len(offsets) >= 2 else 0.0
    base_offsets = [offset for offset in offsets if offset <= seconds + 1e-3]
    base_span = base_offsets[-1] - base_offsets[0] if len(base_offsets) >= 2 else 0.0
    minimum_frames = max(2, math.floor(minimum_span / step) + 1)
    base_complete = (
        base_span + 1e-3 >= minimum_span and len(base_offsets) >= minimum_frames
    )
    review_complete = (
        span + 1e-3 >= ambiguous_review_minimum_span
        and len(offsets) >= minimum_frames
    )
    start = datetime.fromisoformat(row["start_local"])
    shift_date, shift = shift_fields(start)
    records = []
    for machine, values in observations.items():
        base_values = [value for value in values if value["offset"] <= seconds + 1e-3]
        base_green_values = [value for value in base_values if value["green"]]
        use_review = not base_complete or len(base_green_values) == 1
        classified_values = values if use_review else base_values
        green_values = [value for value in classified_values if value["green"]]
        complete = review_complete if use_review else base_complete
        if not complete:
            status = "unknown"
        elif len(green_values) >= minimum_green_frames:
            status = "running"
        elif (
            use_review
            and len(green_values) == 1
            and green_values[0]["green_pixels"] >= strong_single_frame_green_pixels
        ):
            status = "running"
        elif not green_values:
            status = "stopped"
        else:
            # A single green frame remains unknown even after the deterministic
            # extended window; it is neither a full blink cycle nor a definite stop.
            status = "unknown"
        records.append(
            {
                "episode_id": row["episode_id"],
                "start_local": row["start_local"],
                "shift_date": shift_date,
                "shift": shift,
                "working": int(GREEN.is_working_time(start)),
                "machine": machine,
                "frames_sampled": len(offsets),
                "window_span_seconds": round(span, 3),
                "green_frames": len(green_values),
                "green_offsets": ";".join(str(value["offset"]) for value in green_values),
                "max_green_pixels": max((value["green_pixels"] for value in values), default=0),
                "max_dominance_p995": max((value["dominance_p995"] for value in values), default=0),
                "max_green": max((value["green_max"] for value in values), default=0),
                "status": status,
                "ambiguous_review_extended": int(use_review),
                "decoded_sizes": ";".join(
                    f"{width}x{height}" for width, height in sorted(decoded_sizes)
                ),
                "error": "",
            }
        )
    result = {
        "episode_id": row["episode_id"],
        "start_local": row["start_local"],
        "frames_sampled": len(offsets),
        "window_span_seconds": round(span, 3),
        "records": records,
        "error": "",
    }
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def unknown_result(row: dict[str, str], error: str) -> dict:
    start = datetime.fromisoformat(row["start_local"])
    shift_date, shift = shift_fields(start)
    records = []
    for machine in GREEN.MACHINE_ROIS:
        records.append(
            {
                "episode_id": row["episode_id"],
                "start_local": row["start_local"],
                "shift_date": shift_date,
                "shift": shift,
                "working": int(GREEN.is_working_time(start)),
                "machine": machine,
                "frames_sampled": 0,
                "window_span_seconds": 0,
                "green_frames": 0,
                "green_offsets": "",
                "max_green_pixels": 0,
                "max_dominance_p995": 0,
                "max_green": 0,
                "status": "unknown",
                "ambiguous_review_extended": 0,
                "decoded_sizes": "",
                "error": error,
            }
        )
    return {
        "episode_id": row["episode_id"],
        "start_local": row["start_local"],
        "frames_sampled": 0,
        "window_span_seconds": 0,
        "records": records,
        "error": error,
    }


def write_outputs(
    output: Path, rows: list[dict[str, str]], results: list[dict], settings: dict
) -> str:
    by_episode = {result["episode_id"]: result for result in results}
    all_records = []
    failures = []
    for row in rows:
        result = by_episode.get(row["episode_id"])
        if not result:
            result = unknown_result(row, "missing result")
        all_records.extend(result["records"])
        if result.get("error"):
            failures.append({"episode_id": row["episode_id"], "error": result["error"]})

    metrics_path = output / "window-metrics.csv"
    with metrics_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_records[0]))
        writer.writeheader()
        writer.writerows(all_records)

    summary_rows = []
    periods = (("有效生产时段", True), ("完整12小时", False))
    shift_keys = sorted({(record["shift_date"], record["shift"]) for record in all_records})
    for shift_date, shift in shift_keys:
        for period, effective_only in periods:
            for machine in (*GREEN.MACHINE_ROIS.keys(), "六台合计"):
                group = [
                    record
                    for record in all_records
                    if record["shift_date"] == shift_date
                    and record["shift"] == shift
                    and (not effective_only or record["working"])
                    and (machine == "六台合计" or record["machine"] == machine)
                ]
                running = sum(record["status"] == "running" for record in group)
                stopped = sum(record["status"] == "stopped" for record in group)
                unknown = sum(record["status"] == "unknown" for record in group)
                known = running + stopped
                summary_rows.append(
                    {
                        "shift_date": shift_date,
                        "shift": shift,
                        "period": period,
                        "machine": machine,
                        "planned_samples": len(group),
                        "running": running,
                        "stopped": stopped,
                        "unknown": unknown,
                        "known_coverage": f"{known / len(group):.2%}" if group else "",
                        "running_rate": f"{running / known:.2%}" if known else "",
                        "known_coverage_percent": round(known / len(group) * 100, 6)
                        if group
                        else None,
                        "running_rate_percent": round(running / known * 100, 6)
                        if known
                        else None,
                    }
                )
    with (output / "machine-rates.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)

    payload = {
        "policy_version": GREEN.CONFIG["policy_version"],
        "config": str(GREEN.CONFIG_PATH),
        "settings": settings,
        "episodes": len(rows),
        "machine_windows": len(all_records),
        "failures": failures,
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    quality = GREEN.CONFIG["quality_gate"]
    reasons: list[str] = []
    if failures and quality["fail_closed_on_decode_error"]:
        reasons.append(f"最终仍有{len(failures)}个回放窗口失败")
    expected_size = "x".join(str(item) for item in GREEN.REFERENCE_SIZE)
    observed_sizes = {
        size
        for record in all_records
        for size in str(record.get("decoded_sizes", "")).split(";")
        if size
    }
    if observed_sizes and observed_sizes != {expected_size}:
        reasons.append(
            f"解码画面尺寸{sorted(observed_sizes)}与v2标定{expected_size}不一致"
        )

    effective_rows = [
        row
        for row in summary_rows
        if row["period"] == "有效生产时段"
        and row["machine"] in GREEN.MACHINE_ROIS
        and row["planned_samples"]
    ]
    if quality["require_all_six_machines"]:
        represented = {row["machine"] for row in effective_rows}
        missing = sorted(set(GREEN.MACHINE_ROIS) - represented)
        if missing:
            reasons.append("有效生产时段缺少机台：" + "、".join(missing))
    minimum_coverage = float(quality["minimum_known_coverage_percent"])
    low_coverage = [
        row
        for row in effective_rows
        if float(row["known_coverage_percent"] or 0) < minimum_coverage
    ]
    if low_coverage:
        labels = [f"{row['shift_date']}{row['shift']}{row['machine']}号" for row in low_coverage]
        reasons.append(
            f"已知覆盖率低于{minimum_coverage:g}%：" + "、".join(labels)
        )

    start_times = [datetime.fromisoformat(row["start_local"]) for row in rows]
    if start_times:
        query_minutes = (
            (max(start_times) - min(start_times)).total_seconds() / 60
            + float(GREEN.CONFIG["sampling"]["step_minutes"])
        )
        response_rule = quality[
            "minimum_machines_with_running_sample_for_queries_at_least_minutes"
        ]
        running_machines = {
            record["machine"]
            for record in all_records
            if record["working"] and record["status"] == "running"
        }
        if (
            query_minutes >= float(response_rule["query_minutes"])
            and len(running_machines) < int(response_rule["machines"])
        ):
            reasons.append(
                "长时段内六台灯位均无运行响应，疑似画面移动或灯位失配，不能按0%出结论"
            )

    quality_gate = "pass" if not reasons else "needs_review"
    qc_payload = {
        "policy_version": GREEN.CONFIG["policy_version"],
        "quality_gate": quality_gate,
        "expected_decoded_size": expected_size,
        "observed_decoded_sizes": sorted(observed_sizes),
        "minimum_known_coverage_percent": minimum_coverage,
        "reasons": reasons,
        "failures": failures,
    }
    (output / "qc-summary.json").write_text(
        json.dumps(qc_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    metrics: dict[str, dict[str, object]] = {}
    tolerance = float(quality["maximum_cross_computer_rate_difference_pp"])
    for row in summary_rows:
        prefix = ".".join(
            (str(row["shift_date"]), str(row["shift"]), str(row["period"]), str(row["machine"]))
        )
        if row["known_coverage_percent"] is not None:
            metrics[f"{prefix}.known_coverage"] = {
                "value": row["known_coverage_percent"],
                "unit": "percentage_point",
                "tolerance": tolerance,
            }
        if row["running_rate_percent"] is not None:
            metrics[f"{prefix}.running_rate"] = {
                "value": row["running_rate_percent"],
                "unit": "percentage_point",
                "tolerance": tolerance,
            }
    metrics_payload = {
        "quality_gate": quality_gate,
        "metrics": metrics,
        "notes": reasons
        or [
            "通道49六个固定灯位；每5分钟连续读取10秒，临界点自动延长到20秒；未知点不并入停机。"
        ],
    }
    (output / "metrics-input.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return quality_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episodes", type=Path)
    parser.add_argument("output", type=Path)
    sampling = GREEN.CONFIG["sampling"]
    parser.add_argument("--seconds", type=float, default=sampling["window_seconds"])
    parser.add_argument("--step", type=float, default=sampling["frame_step_seconds"])
    parser.add_argument(
        "--minimum-span", type=float, default=sampling["minimum_span_seconds"]
    )
    parser.add_argument(
        "--minimum-green-frames", type=int, default=sampling["minimum_green_frames"]
    )
    parser.add_argument(
        "--strong-single-frame-green-pixels",
        type=int,
        default=sampling["strong_single_frame_green_pixels"],
    )
    parser.add_argument(
        "--ambiguous-review-seconds",
        type=float,
        default=sampling["ambiguous_review_window_seconds"],
    )
    parser.add_argument(
        "--ambiguous-review-minimum-span",
        type=float,
        default=sampling["ambiguous_review_minimum_span_seconds"],
    )
    parser.add_argument("--threshold", type=int, default=sampling["green_pixel_threshold"])
    parser.add_argument(
        "--dominance-threshold", type=float, default=sampling["dominance_threshold"]
    )
    parser.add_argument(
        "--event-shift-seconds",
        type=float,
        default=sampling["event_shift_seconds"],
        help=(
            "request playback five seconds after the sample; the recorder's "
            "roughly four-second keyframe lead then places the decoded window "
            "at or just after the requested five-minute point"
        ),
    )
    parser.add_argument("--workers", type=int, default=sampling["parallel_workers"])
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    credentials, _ = NVR.MODULE.load_credentials(False, "dws")
    all_rows = list(csv.DictReader(args.episodes.open(encoding="utf-8", newline="")))
    rows = all_rows[max(0, args.start_index - 1) :]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("no episodes selected")

    settings = {
        "seconds": args.seconds,
        "step": args.step,
        "minimum_span": args.minimum_span,
        "minimum_green_frames": args.minimum_green_frames,
        "strong_single_frame_green_pixels": args.strong_single_frame_green_pixels,
        "ambiguous_review_seconds": args.ambiguous_review_seconds,
        "ambiguous_review_minimum_span": args.ambiguous_review_minimum_span,
        "threshold": args.threshold,
        "dominance_threshold": args.dominance_threshold,
        "event_shift_seconds": args.event_shift_seconds,
        "workers": args.workers,
    }
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                decode_episode,
                row,
                credentials,
                args.output,
                args.seconds,
                args.step,
                args.minimum_span,
                args.event_shift_seconds,
                args.threshold,
                args.dominance_threshold,
                args.minimum_green_frames,
                args.strong_single_frame_green_pixels,
                args.ambiguous_review_seconds,
                args.ambiguous_review_minimum_span,
                args.resume,
            ): row
            for row in rows
        }
        completed = 0
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                error = redact_error(f"{type(exc).__name__}: {exc}")
                result = unknown_result(row, error)
            results.append(result)
            completed += 1
            if completed % 10 == 0 or completed == len(rows) or result.get("error"):
                print(
                    json.dumps(
                        {
                            "completed": completed,
                            "episodes": len(rows),
                            "episode": row["episode_id"],
                            "frames": result["frames_sampled"],
                            "error": result.get("error", ""),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    results.sort(key=lambda item: item["episode_id"])
    quality_gate = write_outputs(args.output, rows, results, settings)
    return 0 if quality_gate == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
