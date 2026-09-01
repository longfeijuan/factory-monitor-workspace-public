#!/usr/bin/env python3
"""Read-only H.264 archive sequence sampler for a small set of NVR episodes."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import av
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nvr_h264_snapshots", ROOT / "scripts" / "nvr_h264_snapshots.py")
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


def save(frame, path: Path, max_width: int) -> None:
    image: Image.Image = frame.to_image()
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)), Image.Resampling.LANCZOS)
    image.save(path, format="JPEG", quality=88, optimize=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--seconds", type=float, default=35)
    ap.add_argument(
        "--after-end-seconds",
        type=float,
        help=(
            "when set, cover each full episode through end_local plus this many "
            "seconds; the helper's pre-roll is included automatically"
        ),
    )
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--start-index", type=int, default=1, help="1-based first episode row")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument(
        "--event-shift-seconds",
        type=float,
        default=0.0,
        help="shift the requested event before building the archive URL; use 60 to cancel the helper's one-minute pre-roll",
    )
    ap.add_argument("--max-width", type=int, default=960)
    args = ap.parse_args()
    credentials, _ = MOD.MODULE.load_credentials(False, "dws")
    rows = list(csv.DictReader(args.episodes.open(encoding="utf-8", newline="")))
    rows = rows[max(0, args.start_index - 1) :]
    if args.limit:
        rows = rows[: args.limit]
    def extract(row):
        out_dir = args.output / row["episode_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(out_dir.glob("t*.jpg"))
        if args.resume and existing:
            restored = []
            for path in existing:
                try:
                    offset = float(path.stem[1:])
                except ValueError:
                    continue
                restored.append({"episode_id": row["episode_id"], "event_local": row["start_local"], "offset": offset, "image": str(path)})
            if restored:
                return restored
        event = __import__("datetime").datetime.fromisoformat(row["start_local"]) + timedelta(
            seconds=args.event_shift_seconds
        )
        seconds = args.seconds
        if args.after_end_seconds is not None:
            episode_start = __import__("datetime").datetime.fromisoformat(row["start_local"])
            episode_end = __import__("datetime").datetime.fromisoformat(row["end_local"])
            helper_preroll = 60.0 - args.event_shift_seconds
            seconds = max(
                0.0,
                (episode_end - episode_start).total_seconds()
                + helper_preroll
                + args.after_end_seconds,
            )
        url = MOD.playback_url(row["recorder"], int(row["channel"]) * 100 + 1, event, credentials)
        container = av.open(url, options={"rtsp_transport": "tcp", "stimeout": "15000000"})
        saved = []
        try:
            for frame in container.decode(video=0):
                t = float(frame.time or 0.0)
                if t > seconds:
                    break
                if not saved or t + 1e-3 >= saved[-1]["offset"] + args.step:
                    path = out_dir / f"t{t:05.1f}.jpg"
                    save(frame, path, args.max_width)
                    saved.append({"episode_id": row["episode_id"], "event_local": row["start_local"], "offset": round(t, 3), "image": str(path)})
        finally:
            container.close()
        return saved

    results, failures = [], []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(extract, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                saved = future.result()
                results.extend(saved)
                print(json.dumps({"episode": row["episode_id"], "frames": len(saved)}, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append({"episode_id": row["episode_id"], "error": f"{type(exc).__name__}: {exc}"})
                print(json.dumps({"episodeFailure": row["episode_id"], "error": str(exc)}, ensure_ascii=False), flush=True)
    results.sort(key=lambda item: (item["episode_id"], item["offset"]))
    with (args.output / "snapshots.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["episode_id", "event_local", "offset", "image"])
        writer.writeheader(); writer.writerows(results)
    (args.output / "summary.json").write_text(json.dumps({"episodes": len(rows), "frames": len(results), "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
