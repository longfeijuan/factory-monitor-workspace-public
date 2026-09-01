#!/usr/bin/env python3
"""Analyze six simple-steel machine lamps from channel 59/31 manifests."""

from __future__ import annotations

import argparse
import csv
import json
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
    for candidate in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def read_manifest(path: Path, start: datetime, end: datetime) -> dict[str, str]:
    by_time: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            stamp = row.get("start_local") or row.get("event_local") or row.get("timestamp_local")
            image = row.get("image")
            if not stamp or not image or not Path(image).exists():
                continue
            value = datetime.fromisoformat(stamp)
            if start <= value < end:
                by_time[value.isoformat(timespec="seconds")] = image
    return by_time


def metrics(image: Image.Image, box: tuple[int, int, int, int]) -> dict[str, int | float]:
    rgb = np.asarray(image.crop(box).convert("RGB"), dtype=np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dominance = g - np.maximum(r, b)
    strict = (g >= 85) & (g - r >= 22) & (g - b >= 12)
    vivid = (g >= 130) & (g - r >= 35) & (g - b >= 20)
    return {
        "strict_pixels": int(strict.sum()),
        "vivid_pixels": int(vivid.sum()),
        "dominance_p999": round(float(np.percentile(dominance, 99.9)), 3),
        "roi_pixels": int(rgb.shape[0] * rgb.shape[1]),
    }


def load_overrides(path: Path | None) -> dict[tuple[str, str], tuple[str, str]]:
    if not path or not path.exists():
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            machine = row["machine"].upper().replace("号", "")
            if not machine.startswith("M"):
                machine = f"M{machine}"
            state = row["state"].strip().lower()
            if state not in {"green", "amber", "unknown"}:
                raise ValueError(f"invalid override state: {state}")
            result[(machine, datetime.fromisoformat(row["start_local"]).isoformat(timespec="seconds"))] = (
                state,
                row.get("reason", "manual review"),
            )
    return result


def review_reason(machine: str, vivid: int, threshold: int, invalid: bool) -> str:
    if invalid:
        return "possible full-green decode corruption"
    if machine in {"M2", "M4"} and 0 < vivid < 250:
        return "small/far lamp near threshold"
    if machine == "M3" and 0 < vivid < 1000:
        return "distant lamp weak green"
    if machine in {"M1", "M5", "M6"} and 0 < vivid < 3000:
        return "near lamp weak/partly occluded"
    if abs(vivid - threshold) <= max(20, threshold // 2):
        return "threshold boundary"
    return ""


def flat_green_decode_corruption(values: dict[str, int | float]) -> bool:
    """Detect the flat green H.264 decode blocks seen in historical NVR frames."""
    vivid = int(values["vivid_pixels"])
    strict = int(values["strict_pixels"])
    roi = int(values["roi_pixels"])
    if not vivid or not roi:
        return False
    vivid_fraction = vivid / roi
    strict_vivid_ratio = strict / vivid
    dominance = float(values["dominance_p999"])
    return (
        135.5 <= dominance <= 136.5
        and vivid_fraction >= 0.04
        and strict_vivid_ratio <= 1.15
    )


def build_sheet(rows: list[dict[str, object]], box: tuple[int, int, int, int], output: Path) -> None:
    cell_w, cell_h, cols = 220, 172, 12
    sheet = Image.new("RGB", (cell_w * cols, cell_h * ((len(rows) + cols - 1) // cols)), "white")
    draw = ImageDraw.Draw(sheet)
    label = font()
    colors = {"green": "#00a84f", "amber": "#f59e0b", "unknown": "#dc2626"}
    for index, row in enumerate(rows):
        image = Image.open(str(row["image"])).convert("RGB").crop(box)
        image.thumbnail((cell_w - 6, cell_h - 31))
        x, y = (index % cols) * cell_w, (index // cols) * cell_h
        sheet.paste(image, (x + 3, y + 28))
        state = str(row["state"])
        draw.rectangle((x + 1, y + 26, x + cell_w - 2, y + cell_h - 2), outline=colors[state], width=3)
        draw.text((x + 4, y + 3), f"{str(row['start_local'])[11:16]} {state[0].upper()}", fill="black", font=label)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=91)


def select_qc_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Pick one hardest green and one hardest amber sample per machine."""
    selected: list[dict[str, object]] = []
    for machine in MACHINES:
        machine_rows = [
            row
            for row in rows
            if row["machine"] == machine and row["state"] in {"green", "amber"}
        ]
        for state in ("green", "amber"):
            candidates = [row for row in machine_rows if row["state"] == state]
            if not candidates:
                continue
            selected.append(
                min(
                    candidates,
                    key=lambda row: abs(int(row["vivid_pixels"]) - int(row["threshold"])),
                )
            )
    return selected


def build_qc_mosaic(rows: list[dict[str, object]], output: Path) -> int:
    """Build one bounded-size ROI-only visual spot-check image."""
    selected = select_qc_rows(rows)
    cell_w, cell_h, cols = 300, 210, 3
    mosaic_rows = max(1, (len(selected) + cols - 1) // cols)
    sheet = Image.new("RGB", (cell_w * cols, cell_h * mosaic_rows), "white")
    draw = ImageDraw.Draw(sheet)
    label = font(17)
    state_colors = {"green": "#00a84f", "amber": "#f59e0b"}
    for index, row in enumerate(selected):
        machine = str(row["machine"])
        _, box = MACHINES[machine]
        image = Image.open(str(row["image"])).convert("RGB").crop(box)
        image.thumbnail((cell_w - 12, cell_h - 45))
        x, y = (index % cols) * cell_w, (index // cols) * cell_h
        sheet.paste(image, (x + (cell_w - image.width) // 2, y + 39))
        state = str(row["state"])
        draw.rectangle(
            (x + 2, y + 37, x + cell_w - 3, y + cell_h - 3),
            outline=state_colors[state],
            width=3,
        )
        stamp = str(row["start_local"])[11:16]
        vivid = int(row["vivid_pixels"])
        threshold = int(row["threshold"])
        draw.text(
            (x + 6, y + 7),
            f"{machine} {stamp} {state[0].upper()} vivid={vivid}/{threshold}",
            fill="black",
            font=label,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=72, optimize=True, progressive=True)
    return output.stat().st_size


def is_effective_production_time(stamp: str, shift: str) -> bool:
    """Return whether a five-minute point is outside the fixed shift breaks."""
    value = datetime.fromisoformat(stamp)
    minute = value.hour * 60 + value.minute
    if shift == "day":
        return (8 * 60 <= minute < 12 * 60) or (13 * 60 + 30 <= minute < 17 * 60 + 30) or (
            18 * 60 <= minute < 20 * 60
        )
    return not (0 <= minute < 2 * 60)


def expected_effective_points(start: datetime, end: datetime, shift: str) -> int:
    count = 0
    cursor = start
    while cursor < end:
        if is_effective_production_time(cursor.isoformat(timespec="seconds"), shift):
            count += 1
        cursor += timedelta(minutes=5)
    return count


def summarize_rates(rows: list[dict[str, object]], expected: int) -> list[dict[str, object]]:
    rate_rows: list[dict[str, object]] = []
    total_valid = total_green = 0
    for machine in MACHINES:
        machine_rows = [row for row in rows if row["machine"] == machine]
        valid = [row for row in machine_rows if row["state"] != "unknown"]
        green = sum(row["state"] == "green" for row in valid)
        total_valid += len(valid)
        total_green += green
        rate_rows.append(
            {
                "machine": machine.replace("M", "") + "号",
                "expected_points": expected,
                "decoded_points": len(machine_rows),
                "valid_points": len(valid),
                "green_points": green,
                "runtime_rate": f"{green / len(valid) * 100:.1f}%" if valid else "N/A",
                "coverage": f"{len(valid) / expected * 100:.1f}%" if expected else "N/A",
            }
        )
    rate_rows.append(
        {
            "machine": "六台综合",
            "expected_points": expected * len(MACHINES),
            "decoded_points": len(rows),
            "valid_points": total_valid,
            "green_points": total_green,
            "runtime_rate": f"{total_green / total_valid * 100:.1f}%" if total_valid else "N/A",
            "coverage": (
                f"{total_valid / (expected * len(MACHINES)) * 100:.1f}%" if expected else "N/A"
            ),
        }
    )
    return rate_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ch59-manifest", type=Path, required=True)
    parser.add_argument("--ch31-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shift", choices=("day", "night"), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()

    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if end <= start:
        parser.error("--end must be after --start")
    args.output.mkdir(parents=True, exist_ok=True)
    manifests = {
        59: read_manifest(args.ch59_manifest, start, end),
        31: read_manifest(args.ch31_manifest, start, end),
    }
    overrides = load_overrides(args.overrides)
    thresholds = {machine: 100 for machine in MACHINES}
    if args.shift == "day":
        thresholds["M4"] = 37

    rows: list[dict[str, object]] = []
    for machine, (channel, box) in MACHINES.items():
        for stamp, path in sorted(manifests[channel].items()):
            image = Image.open(path).convert("RGB")
            value = metrics(image, box)
            unexpected_size = image.size != EXPECTED_FRAME_SIZE
            invalid = (
                int(value["vivid_pixels"]) >= int(value["roi_pixels"]) * 0.90
                or flat_green_decode_corruption(value)
            )
            state = (
                "unknown"
                if invalid or unexpected_size
                else ("green" if int(value["vivid_pixels"]) >= thresholds[machine] else "amber")
            )
            reason = review_reason(machine, int(value["vivid_pixels"]), thresholds[machine], invalid)
            if unexpected_size:
                reason = f"unexpected frame size {image.width}x{image.height}"
            elif invalid:
                reason = "flat/full-green decode corruption excluded locally"
            rows.append(
                {
                    "machine": machine,
                    "channel": channel,
                    "start_local": stamp,
                    "image": path,
                    "frame_width": image.width,
                    "frame_height": image.height,
                    **value,
                    "threshold": thresholds[machine],
                    "state": state,
                    "review_reason": reason,
                }
            )

    for row in rows:
        key = (str(row["machine"]), str(row["start_local"]))
        if key in overrides:
            row["state"], row["review_reason"] = overrides[key]

    metric_fields = list(rows[0])
    with (args.output / "green-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        writer.writerows(rows)

    review = [row for row in rows if row["review_reason"]]
    with (args.output / "review-required.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        writer.writerows(review)

    expected = int((end - start) / timedelta(minutes=5))
    for machine, (_, box) in MACHINES.items():
        machine_rows = [row for row in rows if row["machine"] == machine]
        build_sheet(machine_rows, box, args.output / f"{machine.lower()}-light-5min.jpg")

    rate_rows = summarize_rates(rows, expected)
    with (args.output / "preliminary-rates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rate_rows[0]))
        writer.writeheader()
        writer.writerows(rate_rows)

    effective_rows = [
        row
        for row in rows
        if is_effective_production_time(str(row["start_local"]), args.shift)
    ]
    effective_expected = expected_effective_points(start, end, args.shift)
    effective_rate_rows = summarize_rates(effective_rows, effective_expected)
    with (args.output / "effective-rates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(effective_rate_rows[0]))
        writer.writeheader()
        writer.writerows(effective_rate_rows)

    qc_mosaic = args.output / "qc-mosaic.jpg"
    qc_mosaic_bytes = build_qc_mosaic(rows, qc_mosaic)
    unexpected_size_rows = [row for row in rows if str(row["review_reason"]).startswith("unexpected frame size")]
    corruption_rows = [row for row in rows if "decode corruption" in str(row["review_reason"])]
    machine_coverage = {
        str(row["machine"]): float(str(row["coverage"]).rstrip("%"))
        for row in rate_rows
        if row["machine"] != "六台综合" and row["coverage"] != "N/A"
    }
    quality_gate = (
        "pass"
        if machine_coverage
        and min(machine_coverage.values()) >= 95.0
        and not unexpected_size_rows
        and qc_mosaic_bytes <= QC_MOSAIC_MAX_BYTES
        else "review_required"
    )
    qc_summary = {
        "schema_version": 1,
        "quality_gate": quality_gate,
        "analysis_scope": "all decoded five-minute points; local ROI classification",
        "machine_coverage_percent": machine_coverage,
        "decode_corruption_rows_excluded": len(corruption_rows),
        "unexpected_frame_size_rows": len(unexpected_size_rows),
        "diagnostic_candidate_rows": len(review),
        "qc_mosaic": str(qc_mosaic),
        "qc_mosaic_samples": len(select_qc_rows(rows)),
        "qc_mosaic_bytes": qc_mosaic_bytes,
        "cloud_review_policy": {
            "default_max_images": 1,
            "default_max_total_bytes": QC_MOSAIC_MAX_BYTES,
            "allowed_image": "qc-mosaic.jpg",
            "forbidden": ["m1-light-5min.jpg..m6-light-5min.jpg", "raw 2560x1440 frames"],
        },
    }
    with (args.output / "qc-summary.json").open("w", encoding="utf-8") as handle:
        json.dump(qc_summary, handle, ensure_ascii=False, indent=2)
    metrics_input: dict[str, object] = {
        "quality_gate": "pass" if quality_gate == "pass" else "needs_review",
        "metrics": {},
        "notes": [
            f"decoded_bad_frames_excluded={len(corruption_rows)}",
            f"diagnostic_candidates={len(review)}",
        ],
    }
    metrics_output = metrics_input["metrics"]
    assert isinstance(metrics_output, dict)
    for row in effective_rate_rows:
        rate = str(row["runtime_rate"])
        if rate == "N/A":
            metrics_input["quality_gate"] = "needs_review"
            continue
        machine = (
            "overall"
            if row["machine"] == "六台综合"
            else f"M{str(row['machine']).replace('号', '')}"
        )
        metrics_output[f"effective.{machine}.runtime_rate"] = {
            "value": float(rate.rstrip("%")),
            "unit": "percentage_point",
            "tolerance": 1.0,
        }
    raw_overall = next(row for row in rate_rows if row["machine"] == "六台综合")
    if raw_overall["runtime_rate"] != "N/A":
        metrics_output["full_shift.overall.runtime_rate"] = {
            "value": float(str(raw_overall["runtime_rate"]).rstrip("%")),
            "unit": "percentage_point",
            "tolerance": 1.0,
        }
    else:
        metrics_input["quality_gate"] = "needs_review"
    with (args.output / "metrics-input.json").open("w", encoding="utf-8") as handle:
        json.dump(metrics_input, handle, ensure_ascii=False, indent=2)
    print("effective production-period rates")
    for row in effective_rate_rows:
        print(row)
    print("full-shift raw rates")
    for row in rate_rows:
        print(row)
    print(
        f"diagnostic_candidates={len(review)} quality_gate={quality_gate} "
        f"qc_mosaic_bytes={qc_mosaic_bytes} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
