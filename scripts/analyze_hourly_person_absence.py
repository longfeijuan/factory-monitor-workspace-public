#!/usr/bin/env python3
import csv, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAME_ROOT = ROOT / "audit-output/2026-08-10-simple-drill-last-week-frames"
EPISODES = FRAME_ROOT.parent / "2026-08-10-simple-drill-last-week-episodes.csv"

def shift(hour: int) -> str:
    return "白班" if 8 <= hour < 20 else "夜班"

def main() -> int:
    path_to_time = {}
    with EPISODES.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            path = str(FRAME_ROOT / row["episode_id"] / "frame.jpg")
            path_to_time[path] = row["start_local"]
    rows = []
    unmapped = 0
    for line in sys.stdin:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = str(obj.get("path", ""))
        event = path_to_time.get(path) or path_to_time.get(str(Path(path).resolve()))
        if not event:
            unmapped += 1
            continue
        rows.append({"dt": datetime.fromisoformat(event), "count": int(obj.get("count", 0)), "path": path})
    rows.sort(key=lambda row: row["dt"])
    print(f"detections={len(rows)} unmapped={unmapped}")

    runs = []
    current = []
    previous = None
    previous_shift = None
    for row in rows:
        current_shift = shift(row["dt"].hour)
        no_person = row["count"] == 0
        contiguous = previous is not None and (row["dt"] - previous).total_seconds() <= 3660
        if no_person and contiguous and current_shift == previous_shift:
            current.append(row)
        elif no_person:
            if current:
                runs.append(current)
            current = [row]
        else:
            if current:
                runs.append(current)
            current = []
        previous = row["dt"]
        previous_shift = current_shift
    if current:
        runs.append(current)

    ranked = []
    for run in runs:
        start = run[0]["dt"]
        last = run[-1]["dt"]
        ranked.append({
            "shift": shift(start.hour),
            "start": start.isoformat(timespec="minutes"),
            "last_no_person_sample": last.isoformat(timespec="minutes"),
            "sample_count": len(run),
            "observed_span_minutes": int((last - start).total_seconds() / 60),
            "next_sample": None,
        })
    for item in sorted(ranked, key=lambda row: (row["sample_count"], row["observed_span_minutes"]), reverse=True)[:20]:
        print(json.dumps(item, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
