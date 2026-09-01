#!/usr/bin/env python3
"""Summarize multi-day first-floor CNC green-light samples by shift date."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def shift_key(value: datetime) -> tuple[str, str]:
    if 8 <= value.hour < 20:
        return value.date().isoformat(), "白班"
    shift_date = value.date() if value.hour >= 20 else value.date() - timedelta(days=1)
    return shift_date.isoformat(), "夜班"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metrics_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    args = parser.parse_args()

    effective: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    raw: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])

    with args.metrics_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = datetime.fromisoformat(row["start_local"])
            shift_date, shift = shift_key(value)
            machine = row["machine"]
            running = int(row["running"])
            raw[(shift_date, shift, machine)][0] += 1
            raw[(shift_date, shift, machine)][1] += running
            raw[(shift_date, shift, "六台合计")][0] += 1
            raw[(shift_date, shift, "六台合计")][1] += running
            if int(row["working"]):
                effective[(shift_date, shift, machine)][0] += 1
                effective[(shift_date, shift, machine)][1] += running
                effective[(shift_date, shift, "六台合计")][0] += 1
                effective[(shift_date, shift, "六台合计")][1] += running

    fields = [
        "shift_date",
        "shift",
        "machine",
        "effective_samples",
        "effective_running",
        "effective_rate",
        "effective_machine_hours",
        "raw_samples",
        "raw_running",
        "raw_rate",
        "raw_machine_hours",
    ]
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        keys = sorted(effective, key=lambda key: (key[0], 0 if key[1] == "白班" else 1, key[2]))
        for shift_date, shift, machine in keys:
            effective_samples, effective_running = effective[(shift_date, shift, machine)]
            raw_samples, raw_running = raw[(shift_date, shift, machine)]
            writer.writerow(
                {
                    "shift_date": shift_date,
                    "shift": shift,
                    "machine": machine,
                    "effective_samples": effective_samples,
                    "effective_running": effective_running,
                    "effective_rate": f"{effective_running / effective_samples:.1%}",
                    "effective_machine_hours": f"{effective_running * 5 / 60:.2f}",
                    "raw_samples": raw_samples,
                    "raw_running": raw_running,
                    "raw_rate": f"{raw_running / raw_samples:.1%}",
                    "raw_machine_hours": f"{raw_running * 5 / 60:.2f}",
                }
            )


if __name__ == "__main__":
    main()
