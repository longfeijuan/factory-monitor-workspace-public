#!/usr/bin/env python3
"""Read-only, bounded Caiduo NVR machine-runtime analysis."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import quote

import av
import numpy as np


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "references" / "cameras.json"
MAX_WINDOW_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Credential:
    host: str
    username: str
    password: str


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def load_camera(config_path: Path, camera_id: str) -> dict[str, Any]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    normalized = camera_id.removeprefix("cam-nvr-caiduo-").removeprefix("通道")
    normalized = normalized.zfill(3)
    camera = payload.get("cameras", {}).get(normalized)
    if not isinstance(camera, dict) or camera.get("status") != "validated":
        raise SystemExit("machine_runtime_camera_not_configured")
    return camera


def resolve_connector_path(explicit: Path | None = None) -> Path:
    """Find the shared credential loader without assuming a host operating system."""
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if path.is_file():
            return path
        raise SystemExit("nvr_connector_unavailable")

    candidates = [Path.cwd() / "connector" / "gate_nvr_service.py"]
    candidates.extend(
        parent / "connector" / "gate_nvr_service.py"
        for parent in Path(__file__).resolve().parents
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit("nvr_connector_unavailable")


def _load_connector_module(path: Path) -> Any:
    module_name = "_factory_monitor_gate_nvr_service"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SystemExit("nvr_connector_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise SystemExit("nvr_connector_unavailable") from None
    return module


def load_credential(
    recorder: str,
    *,
    import_from_dingtalk: bool,
    dws: str | None,
    connector_path: Path | None,
) -> tuple[Credential, str]:
    """Load one recorder credential in process; never print or persist it here."""
    connector = _load_connector_module(resolve_connector_path(connector_path))
    try:
        dws_command = dws or str(connector.default_dws())
        credentials, source = connector.load_credentials(
            import_from_dingtalk, dws_command
        )
        stored = credentials.get(recorder)
        if stored is None:
            raise KeyError(recorder)
        credential = Credential(
            host=str(stored.host),
            username=str(stored.username),
            password=str(stored.password),
        )
    except Exception:
        raise SystemExit("nvr_credentials_unavailable") from None
    return credential, str(source)


def sample_times(start: datetime, end: datetime, seconds: int) -> list[datetime]:
    values: list[datetime] = []
    cursor = start
    while cursor < end:
        values.append(cursor)
        cursor += timedelta(seconds=seconds)
    return values


def playback_url(credential: Credential, track_id: int, at: datetime) -> str:
    # Explicit local offset is required for recently active Hikvision segments.
    start_text = quote(at.strftime("%Y%m%dT%H%M%S%z"), safe="")
    end_text = quote(
        (at + timedelta(seconds=25)).strftime("%Y%m%dT%H%M%S%z"), safe=""
    )
    return (
        f"rtsp://{quote(credential.username, safe='')}:"
        f"{quote(credential.password, safe='')}@{credential.host}"
        f"/Streaming/tracks/{track_id}?starttime={start_text}&endtime={end_text}"
    )


def inspect_frame(frame: Any, camera: dict[str, Any]) -> dict[str, Any]:
    image = np.asarray(frame.to_image().convert("RGB"), dtype=np.int16)
    height, width = image.shape[:2]
    ref_width, ref_height = camera["roi_reference_resolution"]
    x1, y1, x2, y2 = camera["roi"]
    x1, x2 = round(width * x1 / ref_width), round(width * x2 / ref_width)
    y1, y2 = round(height * y1 / ref_height), round(height * y2 / ref_height)
    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        raise RuntimeError("invalid_roi")
    red, green, blue = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    detector = camera["detector"]
    strong_green = (
        (green > detector["green_min"])
        & (green - red > detector["green_minus_red_min"])
        & (green - blue > detector["green_minus_blue_min"])
    )
    strong_amber = (
        (red > detector["amber_red_min"])
        & (green > detector["amber_green_min"])
        & (red - green > detector["amber_red_minus_green_min"])
        & (green - blue > detector["amber_green_minus_blue_min"])
    )
    return {
        "width": width,
        "height": height,
        "strong_green_pixels": int(strong_green.sum()),
        "strong_amber_pixels": int(strong_amber.sum()),
    }


def decode_observation(
    credential: Credential,
    camera: dict[str, Any],
    at: datetime,
    save_path: Path | None = None,
) -> dict[str, Any]:
    url = playback_url(credential, int(camera["track_id"]), at)
    container = av.open(
        url,
        options={"rtsp_transport": "tcp", "stimeout": "8000000"},
        timeout=(8, 8),
    )
    try:
        observations: list[tuple[Any, dict[str, Any]]] = []
        for index, frame in enumerate(container.decode(video=0)):
            observations.append((frame, inspect_frame(frame, camera)))
            if index >= 7:
                break
        if not observations:
            raise RuntimeError("no_decoded_frames")
        best_frame, peak = max(
            observations,
            key=lambda item: max(
                item[1]["strong_green_pixels"], item[1]["strong_amber_pixels"]
            ),
        )
        if save_path is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            best_frame.to_image().save(save_path, quality=90)
        return {
            **peak,
            "decoded_frame_count": len(observations),
            "peak_strong_green_pixels": max(
                item[1]["strong_green_pixels"] for item in observations
            ),
            "peak_strong_amber_pixels": max(
                item[1]["strong_amber_pixels"] for item in observations
            ),
        }
    finally:
        container.close()


def fetch_one(
    credential: Credential, camera: dict[str, Any], at: datetime
) -> dict[str, Any]:
    try:
        return {
            "sample_at": at.isoformat(),
            "state": "measured",
            **decode_observation(credential, camera, at),
        }
    except Exception as error:  # PyAV exposes several backend-specific errors.
        return {
            "sample_at": at.isoformat(),
            "state": "unknown",
            "reason": error.__class__.__name__,
        }


def collect_samples(
    credential: Credential,
    camera: dict[str, Any],
    times: list[datetime],
    workers: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(fetch_one, credential, camera, at) for at in times
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    return sorted(rows, key=lambda item: item["sample_at"])


def retry_unknown(
    rows: list[dict[str, Any]],
    credential: Credential,
    camera: dict[str, Any],
    workers: int,
    retries: int,
) -> list[dict[str, Any]]:
    by_time = {row["sample_at"]: row for row in rows}
    for _ in range(retries):
        unknown_times = [
            parse_time(key) for key, row in by_time.items() if row["state"] == "unknown"
        ]
        if not unknown_times:
            break
        for row in collect_samples(
            credential, camera, unknown_times, min(workers, 3)
        ):
            if row["state"] == "measured":
                by_time[row["sample_at"]] = row
    return [by_time[key] for key in sorted(by_time)]


def classify_rows(rows: list[dict[str, Any]], camera: dict[str, Any]) -> None:
    threshold = camera["detector"]["running_green_pixels_threshold"]
    for row in rows:
        if row["state"] == "unknown":
            row["classification"] = "unknown"
        elif row["peak_strong_green_pixels"] >= threshold:
            row["classification"] = "running"
        else:
            row["classification"] = "stopped"


def build_intervals(
    rows: list[dict[str, Any]], end: datetime
) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        status = rows[index]["classification"]
        cursor = index + 1
        while cursor < len(rows) and rows[cursor]["classification"] == status:
            cursor += 1
        start_at = parse_time(rows[index]["sample_at"])
        end_at = parse_time(rows[cursor]["sample_at"]) if cursor < len(rows) else end
        end_at = min(end_at, end)
        intervals.append(
            {
                "status": status,
                "start": start_at.isoformat(),
                "end": end_at.isoformat(),
                "duration_seconds": max(0, int((end_at - start_at).total_seconds())),
            }
        )
        index = cursor
    return intervals


def duration_text(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


def save_evidence(
    intervals: list[dict[str, Any]],
    credential: Credential,
    camera: dict[str, Any],
    output_dir: Path,
) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for status in ("running", "stopped"):
        candidates = [item for item in intervals if item["status"] == status]
        if not candidates:
            continue
        chosen = max(candidates, key=lambda item: item["duration_seconds"])
        start_at = parse_time(chosen["start"])
        midpoint = start_at + timedelta(seconds=chosen["duration_seconds"] // 2)
        path = output_dir / "evidence" / f"{status}-{midpoint.strftime('%Y%m%d-%H%M%S')}.jpg"
        try:
            decode_observation(credential, camera, midpoint, save_path=path)
            evidence.append(
                {"status": status, "at": midpoint.isoformat(), "path": str(path.resolve())}
            )
        except Exception:
            continue
    return evidence


def build_result(
    camera: dict[str, Any],
    start: datetime,
    end: datetime,
    interval_seconds: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    intervals = build_intervals(rows, end)
    totals = Counter()
    for item in intervals:
        totals[item["status"]] += item["duration_seconds"]
    valid_seconds = totals["running"] + totals["stopped"]
    window_seconds = int((end - start).total_seconds())
    known_rate = round(totals["running"] / valid_seconds * 100, 2) if valid_seconds else None
    lower = round(totals["running"] / window_seconds * 100, 2)
    upper = round((totals["running"] + totals["unknown"]) / window_seconds * 100, 2)
    transitions = sum(
        intervals[index - 1]["status"] == "stopped"
        and intervals[index]["status"] == "running"
        for index in range(1, len(intervals))
    )
    measured_running = [
        row["peak_strong_green_pixels"]
        for row in rows
        if row["classification"] == "running"
    ]
    measured_stopped = [
        row["peak_strong_green_pixels"]
        for row in rows
        if row["classification"] == "stopped"
    ]
    running_min = min(measured_running) if measured_running else None
    stopped_max = max(measured_stopped) if measured_stopped else None
    signal_separated = (
        True
        if running_min is None or stopped_max is None
        else running_min > stopped_max
    )
    longest_unknown = max(
        (item["duration_seconds"] for item in intervals if item["status"] == "unknown"),
        default=0,
    )
    unknown_fraction = totals["unknown"] / window_seconds if window_seconds else 1
    complete = unknown_fraction <= 0.02 and longest_unknown < 300 and signal_separated
    return {
        "schema_version": "caiduo-machine-runtime/v2",
        "camera_id": camera["camera_id"],
        "machine_name": camera["machine_name"],
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "seconds": window_seconds,
        },
        "method": {
            "signal": camera["detector"]["type"],
            "sampling_interval_seconds": interval_seconds,
            "roi_reference_resolution": camera["roi_reference_resolution"],
            "roi": camera["roi"],
            "running_threshold_green_pixels": camera["detector"]["running_green_pixels_threshold"],
        },
        "sample_counts": dict(Counter(row["classification"] for row in rows)),
        "running_seconds": totals["running"],
        "stopped_seconds": totals["stopped"],
        "unknown_seconds": totals["unknown"],
        "valid_coverage_percent": round(valid_seconds / window_seconds * 100, 2),
        "runtime_rate_percent_of_valid": known_rate,
        "runtime_rate_full_window_lower_bound_percent": lower,
        "runtime_rate_full_window_upper_bound_percent": upper,
        "stop_to_run_transition_count": transitions,
        "quality": {
            "complete": complete,
            "longest_unknown_seconds": longest_unknown,
            "signal_separated": signal_separated,
            "running_green_min": running_min,
            "stopped_green_max": stopped_max,
        },
        "intervals": intervals,
    }


def write_report(result: dict[str, Any], path: Path) -> None:
    window = result["window"]
    quality = result["quality"]
    lines = [
        f"# {result['camera_id']} {result['machine_name']}运行情况",
        "",
        f"- 时段：{window['start']} 至 {window['end']}",
        f"- 运行率（有效画面）：**{result['runtime_rate_percent_of_valid']}%**",
        f"- 运行：{duration_text(result['running_seconds'])}",
        f"- 未运行：{duration_text(result['stopped_seconds'])}",
        f"- unknown：{duration_text(result['unknown_seconds'])}",
        f"- 有效覆盖：{result['valid_coverage_percent']}%",
        f"- 停机→运行次数：{result['stop_to_run_transition_count']}",
        f"- 抽样间隔：{result['method']['sampling_interval_seconds']}秒",
        f"- 质量状态：{'完整' if quality['complete'] else '需人工复核'}",
        "",
        "## 未运行时段",
        "",
        "| 开始 | 结束 | 持续时间 |",
        "|---|---|---:|",
    ]
    stopped = [item for item in result["intervals"] if item["status"] == "stopped"]
    if stopped:
        for item in stopped:
            lines.append(
                f"| {item['start']} | {item['end']} | {duration_text(item['duration_seconds'])} |"
            )
    else:
        lines.append("| — | — | 0秒 |")
    lines.extend(
        [
            "",
            "状态灯仅表示机台显示运行，不代表持续切削或有效产出。时间边界精度约为一个抽样间隔。",
        ]
    )
    if not quality["complete"]:
        lines.extend(
            [
                "",
                "本次存在较多unknown或信号未完全分离，完整时窗运行率只能落在",
                f"{result['runtime_rate_full_window_lower_bound_percent']}%–{result['runtime_rate_full_window_upper_bound_percent']}%之间。",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="材多机台开机率只读核查")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--interval-seconds", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--import-from-dingtalk",
        action="store_true",
        help="从已授权钉钉会话临时导入NVR只读凭据；Windows首次及每次新进程使用",
    )
    parser.add_argument("--dws", help="dws可执行文件路径；默认自动查找")
    parser.add_argument(
        "--connector-path",
        type=Path,
        help="共享NVR连接器路径；默认从项目根目录自动查找",
    )
    args = parser.parse_args()

    camera = load_camera(args.config, args.camera_id)
    start, end = parse_time(args.start), parse_time(args.end)
    window_seconds = (end - start).total_seconds()
    if window_seconds <= 0 or window_seconds > MAX_WINDOW_SECONDS:
        raise SystemExit("analysis_window_must_be_between_0_and_24_hours")
    interval_seconds = args.interval_seconds or int(camera["sampling_interval_seconds"])
    if interval_seconds < 60 or interval_seconds > 3600:
        raise SystemExit("sampling_interval_out_of_range")
    if args.workers < 1 or args.workers > 8 or args.retries < 0 or args.retries > 3:
        raise SystemExit("resource_limit_out_of_range")

    credential, credential_source = load_credential(
        str(camera["recorder"]),
        import_from_dingtalk=args.import_from_dingtalk,
        dws=args.dws,
        connector_path=args.connector_path,
    )
    times = sample_times(start, end, interval_seconds)
    rows = collect_samples(credential, camera, times, args.workers)
    rows = retry_unknown(rows, credential, camera, args.workers, args.retries)
    classify_rows(rows, camera)
    result = build_result(camera, start, end, interval_seconds, rows)
    result["credential_source"] = credential_source

    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence = save_evidence(
        result["intervals"], credential, camera, args.output_dir
    )
    result["evidence"] = evidence
    (args.output_dir / "samples.json").write_text(
        json.dumps(
            {
                "schema_version": "caiduo-machine-runtime-samples/v2",
                "camera_id": camera["camera_id"],
                "machine_name": camera["machine_name"],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (args.output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(result, args.output_dir / "report.md")
    print(
        json.dumps(
            {
                "camera_id": result["camera_id"],
                "machine_name": result["machine_name"],
                "window": result["window"],
                "runtime_rate_percent_of_valid": result["runtime_rate_percent_of_valid"],
                "running_seconds": result["running_seconds"],
                "stopped_seconds": result["stopped_seconds"],
                "unknown_seconds": result["unknown_seconds"],
                "valid_coverage_percent": result["valid_coverage_percent"],
                "stop_to_run_transition_count": result["stop_to_run_transition_count"],
                "quality": result["quality"],
                "output_dir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["quality"]["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
