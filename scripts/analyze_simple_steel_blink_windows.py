#!/usr/bin/env python3
"""Analyze the six simple-steel lamps from timestamp-verified video windows.

Each five-minute point is represented by a roughly ten-second sequence sampled
at 0.4-second intervals. A point is running only when at least two different
frames show a valid green lamp. A complete readable window with no green is
stopped; incomplete, timestamp-misaligned, corrupt, or one-green-frame windows
remain unknown.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


MACHINES = {
    "M1": (59, (1460, 650, 1740, 900)),
    "M2": (59, (1300, 470, 1430, 590)),
    "M3": (59, (1900, 525, 2130, 735)),
    "M4": (59, (1530, 390, 1740, 620)),
    "M5": (31, (1780, 660, 2030, 900)),
    "M6": (31, (1510, 330, 1760, 575)),
}
EXPECTED_FRAME_SIZE = (2560, 1440)
QC_MOSAIC_MAX_BYTES = 750_000


def font(size: int = 17):
    for candidate in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def green_metrics(image: Image.Image, box: tuple[int, int, int, int]) -> dict[str, int | float]:
    rgb = np.asarray(image.crop(box).convert("RGB"), dtype=np.int16)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dominance = green - np.maximum(red, blue)
    strict = (green >= 85) & (green - red >= 22) & (green - blue >= 12)
    vivid = (green >= 130) & (green - red >= 35) & (green - blue >= 20)
    return {
        "strict_pixels": int(strict.sum()),
        "vivid_pixels": int(vivid.sum()),
        "dominance_p999": round(float(np.percentile(dominance, 99.9)), 3),
        "roi_pixels": int(rgb.shape[0] * rgb.shape[1]),
        "roi_stddev": round(float(rgb.std()), 3),
    }


def flat_green_decode_corruption(values: dict[str, int | float]) -> bool:
    vivid = int(values["vivid_pixels"])
    strict = int(values["strict_pixels"])
    roi = int(values["roi_pixels"])
    if not vivid or not roi:
        return False
    return (
        135.5 <= float(values["dominance_p999"]) <= 136.5
        and vivid / roi >= 0.04
        and strict / vivid <= 1.15
    )


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for index, a in enumerate(left, start=1):
        current = [index]
        for column, b in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (a != b),
                )
            )
        previous = current
    return previous[-1]


def parse_osd(items: list[dict], expected: datetime) -> tuple[datetime | None, str]:
    # The NVR timestamp is confined to the top strip. Ignoring machine labels
    # elsewhere prevents their digits from being mistaken for the clock.
    ordered = sorted(
        (item for item in items if int(item.get("y", 0)) < 200),
        key=lambda item: int(item.get("x", 0)),
    )
    text = " ".join(str(item.get("text", "")) for item in ordered)
    normalized = text.translate(
        str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "Z": "2", "z": "2", "B": "8"})
    )
    digits = "".join(re.findall(r"\d+", normalized))
    if len(digits) < 11:
        return None, text
    expected_date = expected.strftime("%Y%m%d")
    cuts = [
        (edit_distance(digits[:cut], expected_date), cut)
        for cut in range(6, min(12, len(digits) - 3) + 1)
    ]
    date_score, cut = min(cuts)
    if date_score > 3:
        return None, text
    clock_digits = digits[cut:]
    candidates = []
    for delta in range(-30, 31):
        candidate = expected + timedelta(seconds=delta)
        score = edit_distance(clock_digits, candidate.strftime("%H%M%S"))
        candidates.append((score, abs(delta), candidate))
    score, _, candidate = min(candidates)
    if score > 2:
        return None, text
    return candidate.replace(microsecond=0), text


def run_ocr(binary: Path, paths: list[Path]) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for index in range(0, len(paths), 96):
        batch = paths[index : index + 96]
        completed = subprocess.run(
            [str(binary), *(str(path) for path in batch)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        for line in completed.stdout.splitlines():
            payload = json.loads(line)
            results[str(payload["image"])] = list(payload.get("items", []))
    return results


def read_manifest(path: Path) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            image = Path(row["image"])
            if not image.exists():
                continue
            grouped[row["event_local"]].append(
                {
                    "offset": float(row["media_offset_seconds"]),
                    "image": str(image),
                }
            )
    for rows in grouped.values():
        rows.sort(key=lambda row: float(row["offset"]))
    return grouped


def merge_manifests(paths: list[Path]) -> dict[str, list[dict[str, object]]]:
    merged: dict[str, list[dict[str, object]]] = {}
    for path in paths:
        # Later retry manifests replace the original window for that timestamp.
        merged.update(read_manifest(path))
    return merged


def read_overrides(path: Path | None) -> dict[tuple[str, str], tuple[str, str]]:
    if not path or not path.exists():
        return {}
    overrides = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            machine = row["machine"].upper().replace("号", "")
            if not machine.startswith("M"):
                machine = f"M{machine}"
            state = row["state"].strip().lower()
            if state not in {"running", "stopped", "unknown"}:
                raise ValueError(f"invalid override state: {state}")
            stamp = datetime.fromisoformat(row["start_local"]).isoformat(timespec="seconds")
            overrides[(machine, stamp)] = (state, row.get("reason", "manual QC"))
    return overrides


def is_effective(stamp: str, shift: str) -> bool:
    value = datetime.fromisoformat(stamp)
    minute = value.hour * 60 + value.minute
    if shift == "day":
        return (
            8 * 60 <= minute < 12 * 60
            or 13 * 60 + 30 <= minute < 17 * 60 + 30
            or 18 * 60 <= minute < 20 * 60
        )
    return not (0 <= minute < 2 * 60)


def summarize(records: list[dict[str, object]], expected_per_machine: int) -> list[dict[str, object]]:
    rows = []
    total_running = total_stopped = total_unknown = 0
    for machine in MACHINES:
        group = [record for record in records if record["machine"] == machine]
        running = sum(record["state"] == "running" for record in group)
        stopped = sum(record["state"] == "stopped" for record in group)
        unknown = expected_per_machine - running - stopped
        known = running + stopped
        total_running += running
        total_stopped += stopped
        total_unknown += unknown
        rows.append(
            {
                "machine": machine.replace("M", "") + "号",
                "expected_points": expected_per_machine,
                "running_points": running,
                "stopped_points": stopped,
                "unknown_points": unknown,
                "valid_points": known,
                "runtime_rate": f"{running / known * 100:.1f}%" if known else "N/A",
                "coverage": f"{known / expected_per_machine * 100:.1f}%",
            }
        )
    total_expected = expected_per_machine * len(MACHINES)
    total_known = total_running + total_stopped
    rows.append(
        {
            "machine": "六台综合",
            "expected_points": total_expected,
            "running_points": total_running,
            "stopped_points": total_stopped,
            "unknown_points": total_unknown,
            "valid_points": total_known,
            "runtime_rate": f"{total_running / total_known * 100:.1f}%" if total_known else "N/A",
            "coverage": f"{total_known / total_expected * 100:.1f}%",
        }
    )
    return rows


def choose_frames(detail: list[dict[str, object]], state: str) -> list[dict[str, object]]:
    valid = [frame for frame in detail if frame["frame_state"] != "unknown"]
    if not valid:
        return []
    if state == "running":
        greens = [frame for frame in valid if frame["frame_state"] == "green"]
        nongreens = [frame for frame in valid if frame["frame_state"] == "amber"]
        selected = []
        if greens:
            selected.append(greens[0])
        if nongreens:
            selected.append(nongreens[len(nongreens) // 2])
        elif valid:
            selected.append(valid[len(valid) // 2])
        if len(greens) > 1:
            selected.append(greens[-1])
        elif valid:
            selected.append(valid[-1])
        return sorted(selected[:3], key=lambda frame: float(frame["offset"]))
    return [valid[0], valid[len(valid) // 2], valid[-1]]


def build_qc_mosaic(
    records: list[dict[str, object]],
    details: dict[tuple[str, str], list[dict[str, object]]],
    output: Path,
) -> int:
    cell_w, cell_h = 210, 150
    sheet = Image.new("RGB", (cell_w * 6, cell_h * 6), "white")
    draw = ImageDraw.Draw(sheet)
    label = font(14)
    colors = {"green": "#00a84f", "amber": "#f59e0b", "unknown": "#dc2626"}
    for row_index, machine in enumerate(MACHINES):
        machine_rows = [record for record in records if record["machine"] == machine]
        running = sorted(
            (record for record in machine_rows if record["state"] == "running"),
            key=lambda record: int(record["green_frames"]),
        )
        stopped = [record for record in machine_rows if record["state"] == "stopped"]
        samples = [running[0] if running else None, stopped[0] if stopped else None]
        for sample_index, record in enumerate(samples):
            base_col = sample_index * 3
            frames = [] if record is None else choose_frames(
                details.get((machine, str(record["start_local"])), []), str(record["state"])
            )
            for frame_index in range(3):
                x, y = (base_col + frame_index) * cell_w, row_index * cell_h
                if frame_index >= len(frames) or record is None:
                    draw.text((x + 6, y + 8), f"{machine} no sample", fill="black", font=label)
                    continue
                frame = frames[frame_index]
                _, box = MACHINES[machine]
                image = Image.open(str(frame["image"])).convert("RGB").crop(box)
                image.thumbnail((cell_w - 8, cell_h - 34))
                sheet.paste(image, (x + (cell_w - image.width) // 2, y + 30))
                frame_state = str(frame["frame_state"])
                draw.rectangle(
                    (x + 2, y + 28, x + cell_w - 3, y + cell_h - 3),
                    outline=colors[frame_state],
                    width=3,
                )
                if frame_index == 0:
                    state_letter = "R" if record["state"] == "running" else "S"
                    text = (
                        f"{machine} {state_letter} {str(record['start_local'])[11:16]} "
                        f"g={record['green_frames']}/{record['valid_frames']}"
                    )
                else:
                    text = f"+{float(frame['offset']):.1f}s {frame_state[0].upper()}"
                draw.text((x + 5, y + 5), text, fill="black", font=label)
    output.parent.mkdir(parents=True, exist_ok=True)
    quality = 74
    while True:
        sheet.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
        if output.stat().st_size <= QC_MOSAIC_MAX_BYTES or quality <= 45:
            break
        quality -= 5
    return output.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--ch59-manifest", type=Path, required=True)
    parser.add_argument("--ch31-manifest", type=Path, required=True)
    parser.add_argument("--ch59-retry-manifest", action="append", type=Path, default=[])
    parser.add_argument("--ch31-retry-manifest", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shift", choices=("day", "night"), required=True)
    parser.add_argument("--ocr-bin", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--minimum-span", type=float, default=6.0)
    parser.add_argument("--maximum-interval", type=float, default=0.5)
    parser.add_argument("--minimum-valid-frames", type=int, default=16)
    parser.add_argument("--minimum-green-frames", type=int, default=2)
    parser.add_argument("--limit", type=int, help="analyze only the first N episode rows")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    with args.episodes.open(newline="", encoding="utf-8") as handle:
        episode_rows = list(csv.DictReader(handle))
    if args.limit:
        episode_rows = episode_rows[: args.limit]
    stamps = [datetime.fromisoformat(row["start_local"]).isoformat(timespec="seconds") for row in episode_rows]
    manifests = {
        59: merge_manifests([args.ch59_manifest, *args.ch59_retry_manifest]),
        31: merge_manifests([args.ch31_manifest, *args.ch31_retry_manifest]),
    }
    thresholds = {machine: 100 for machine in MACHINES}
    if args.shift == "day":
        thresholds["M4"] = 37

    ocr_paths: list[Path] = []
    for channel_rows in manifests.values():
        for frames in channel_rows.values():
            if frames:
                ocr_paths.extend([Path(str(frames[0]["image"])), Path(str(frames[-1]["image"]))])
    ocr_results = run_ocr(args.ocr_bin, list(dict.fromkeys(ocr_paths)))

    fallback_paths: list[Path] = []
    for channel_rows in manifests.values():
        for stamp, frames in channel_rows.items():
            if not frames:
                continue
            event = datetime.fromisoformat(stamp)
            first_path = str(frames[0]["image"])
            if parse_osd(ocr_results.get(first_path, []), event)[0] is None:
                fallback_paths.extend(Path(str(frame["image"])) for frame in frames[1:7])
    if fallback_paths:
        ocr_results.update(run_ocr(args.ocr_bin, list(dict.fromkeys(fallback_paths))))

    osd_rows = []
    alignment: dict[tuple[int, str], tuple[bool, float, float, str]] = {}
    for channel in (59, 31):
        for stamp in stamps:
            frames = manifests[channel].get(stamp, [])
            if not frames:
                alignment[(channel, stamp)] = (False, 0.0, 0.0, "no frames")
                osd_rows.append(
                    {
                        "channel": channel,
                        "start_local": stamp,
                        "first_osd": "",
                        "last_osd": "",
                        "first_expected": stamp,
                        "last_expected": "",
                        "first_delta_seconds": "",
                        "last_delta_seconds": "",
                        "osd_span_seconds": "",
                        "media_span_seconds": 0,
                        "aligned": 0,
                        "first_ocr": "",
                        "last_ocr": "",
                        "reason": "no frames",
                    }
                )
                continue
            event = datetime.fromisoformat(stamp)
            first_frame = frames[0]
            first_path = str(first_frame["image"])
            last_path = str(frames[-1]["image"])
            first, first_text = parse_osd(ocr_results.get(first_path, []), event)
            for candidate_frame in frames[1:7]:
                if first is not None:
                    break
                candidate_path = str(candidate_frame["image"])
                candidate, candidate_text = parse_osd(ocr_results.get(candidate_path, []), event)
                if candidate is not None:
                    first_frame = candidate_frame
                    first_path = candidate_path
                    first, first_text = candidate, candidate_text
            expected_last = event + timedelta(seconds=float(frames[-1]["offset"]))
            last, last_text = parse_osd(ocr_results.get(last_path, []), expected_last)
            first_frame_media_offset = float(first_frame["offset"])
            first_offset_seconds = (
                (first - event).total_seconds() - first_frame_media_offset if first else 999.0
            )
            first_delta = abs(first_offset_seconds)
            last_delta = abs((last - expected_last).total_seconds()) if last else 999.0
            media_span = float(frames[-1]["offset"]) - float(frames[0]["offset"])
            osd_span = (last - first).total_seconds() if first and last else None
            # The target point is aligned from the first OSD timestamp. Some
            # channel-31 frames split the final clock glyphs too aggressively
            # for OCR; a missing final OCR read is acceptable because all
            # frames come from the same continuous decoded stream. If the
            # final clock is readable, it must also agree with media time.
            target_media_offset = max(0.0, -first_offset_seconds) if first else 0.0
            usable_span = max(0.0, media_span - target_media_offset)
            last_reaches_minimum = last is None or last >= event + timedelta(seconds=args.minimum_span)
            aligned = bool(
                first
                and -12.0 <= first_offset_seconds <= 0.5
                and usable_span >= args.minimum_span
                and last_reaches_minimum
            )
            reason = "" if aligned else "target OSD mismatch or incomplete continuous window"
            alignment[(channel, stamp)] = (aligned, usable_span, target_media_offset, reason)
            osd_rows.append(
                {
                    "channel": channel,
                    "start_local": stamp,
                    "first_osd": first.isoformat(timespec="seconds") if first else "",
                    "last_osd": last.isoformat(timespec="seconds") if last else "",
                    "first_expected": event.isoformat(timespec="seconds"),
                    "last_expected": expected_last.isoformat(timespec="milliseconds"),
                    "first_delta_seconds": round(first_delta, 3),
                    "first_offset_from_target_seconds": round(first_offset_seconds, 3),
                    "last_delta_seconds": round(last_delta, 3),
                    "osd_span_seconds": osd_span if osd_span is not None else "",
                    "media_span_seconds": round(media_span, 3),
                    "target_media_offset_seconds": round(target_media_offset, 3),
                    "usable_span_seconds": round(usable_span, 3),
                    "aligned": int(aligned),
                    "first_ocr": first_text,
                    "last_ocr": last_text,
                    "reason": reason,
                }
            )
    with (args.output / "osd-validation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(osd_rows[0]))
        writer.writeheader()
        writer.writerows(osd_rows)

    machines_by_channel = {
        channel: [machine for machine, (machine_channel, _) in MACHINES.items() if machine_channel == channel]
        for channel in (59, 31)
    }
    details: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    records = []
    overrides = read_overrides(args.overrides)
    for channel in (59, 31):
        for stamp in stamps:
            all_frames = manifests[channel].get(stamp, [])
            aligned, usable_span, target_media_offset, osd_reason = alignment[(channel, stamp)]
            frames = [
                frame
                for frame in all_frames
                if target_media_offset - 0.15 <= float(frame["offset"]) <= target_media_offset + 10.15
            ]
            offsets = [float(frame["offset"]) for frame in frames]
            intervals = [right - left for left, right in zip(offsets, offsets[1:])]
            max_interval = max(intervals, default=999.0)
            median_interval = statistics.median(intervals) if intervals else 0.0
            for frame in frames:
                image = Image.open(str(frame["image"])).convert("RGB")
                for machine in machines_by_channel[channel]:
                    _, box = MACHINES[machine]
                    values = green_metrics(image, box)
                    invalid = image.size != EXPECTED_FRAME_SIZE or flat_green_decode_corruption(values)
                    frame_state = (
                        "unknown"
                        if invalid
                        else ("green" if int(values["vivid_pixels"]) >= thresholds[machine] else "amber")
                    )
                    details[(machine, stamp)].append(
                        {
                            "offset": float(frame["offset"]),
                            "image": str(frame["image"]),
                            "frame_state": frame_state,
                            "vivid_pixels": int(values["vivid_pixels"]),
                        }
                    )
            for machine in machines_by_channel[channel]:
                window = details[(machine, stamp)]
                valid = [frame for frame in window if frame["frame_state"] != "unknown"]
                green_count = sum(frame["frame_state"] == "green" for frame in valid)
                invalid_count = len(window) - len(valid)
                complete = (
                    aligned
                    and usable_span >= args.minimum_span
                    and max_interval <= args.maximum_interval + 1e-6
                    and len(valid) >= args.minimum_valid_frames
                )
                reasons = []
                if not aligned:
                    reasons.append(osd_reason)
                if max_interval > args.maximum_interval + 1e-6:
                    reasons.append(f"frame interval {max_interval:.3f}s exceeds limit")
                if len(valid) < args.minimum_valid_frames:
                    reasons.append("insufficient valid frames")
                if green_count >= args.minimum_green_frames and complete:
                    state = "running"
                elif complete and green_count == 0 and invalid_count == 0:
                    state = "stopped"
                else:
                    state = "unknown"
                    if green_count == 1:
                        reasons.append("only one isolated green frame")
                    if invalid_count:
                        reasons.append("unreadable/corrupt frame inside non-running window")
                key = (machine, stamp)
                if key in overrides:
                    state, override_reason = overrides[key]
                    reasons.append(override_reason)
                records.append(
                    {
                        "machine": machine,
                        "channel": channel,
                        "start_local": stamp,
                        "effective": int(is_effective(stamp, args.shift)),
                        "frames_sampled": len(window),
                        "valid_frames": len(valid),
                        "green_frames": green_count,
                        "invalid_frames": invalid_count,
                        "media_span_seconds": round(offsets[-1] - offsets[0], 3) if len(offsets) >= 2 else 0,
                        "verified_usable_span_seconds": round(usable_span, 3),
                        "median_interval_seconds": round(median_interval, 3),
                        "max_interval_seconds": round(max_interval, 3),
                        "osd_aligned": int(aligned),
                        "state": state,
                        "review_reason": "; ".join(dict.fromkeys(reasons)),
                    }
                )

    with (args.output / "window-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    review_rows = [
        record
        for record in records
        if record["state"] == "unknown" or int(record["green_frames"]) <= args.minimum_green_frames
    ]
    with (args.output / "review-required.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(review_rows)

    effective_records = [record for record in records if record["effective"]]
    effective_rates = summarize(effective_records, sum(is_effective(stamp, args.shift) for stamp in stamps))
    raw_rates = summarize(records, len(stamps))
    for name, rows in (("effective-rates.csv", effective_rates), ("preliminary-rates.csv", raw_rates)):
        with (args.output / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    mosaic = args.output / "qc-mosaic.jpg"
    mosaic_bytes = build_qc_mosaic(records, details, mosaic)
    full_machine_coverages = {
        row["machine"]: float(str(row["coverage"]).rstrip("%"))
        for row in raw_rates
        if row["machine"] != "六台综合"
    }
    osd_coverage = {
        str(channel): sum(int(row["aligned"]) for row in osd_rows if int(row["channel"]) == channel) / len(stamps) * 100
        for channel in (59, 31)
    }
    quality_gate = (
        "pass"
        if min(full_machine_coverages.values(), default=0) >= 95.0
        and min(osd_coverage.values(), default=0) >= 95.0
        and mosaic_bytes <= QC_MOSAIC_MAX_BYTES
        else "review_required"
    )
    qc = {
        "schema_version": 2,
        "quality_gate": quality_gate,
        "window_seconds_requested": 10.0,
        "sample_interval_seconds_requested": 0.4,
        "minimum_accepted_window_seconds": args.minimum_span,
        "maximum_accepted_interval_seconds": args.maximum_interval,
        "minimum_green_frames_for_running": args.minimum_green_frames,
        "machine_coverage_percent": full_machine_coverages,
        "osd_aligned_windows_percent": osd_coverage,
        "running_windows": sum(record["state"] == "running" for record in records),
        "stopped_windows": sum(record["state"] == "stopped" for record in records),
        "unknown_windows": sum(record["state"] == "unknown" for record in records),
        "qc_mosaic": str(mosaic),
        "qc_mosaic_bytes": mosaic_bytes,
        "cloud_review_policy": {
            "default_max_images": 1,
            "default_max_total_bytes": QC_MOSAIC_MAX_BYTES,
            "allowed_image": "qc-mosaic.jpg",
            "forbidden": ["raw 2560x1440 frames", "per-machine full contact sheets"],
        },
    }
    (args.output / "qc-summary.json").write_text(
        json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(qc, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
