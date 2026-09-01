#!/usr/bin/env python3
"""Summarize channel 15 people observed in the computer-seat review zone."""
from __future__ import annotations
import argparse, csv, json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CONF = 0.25
# 960px extracted channel-15 view: computer/seat side of the table.
ROI = (80, 160, 450, 450)

def persons(row):
    return [d for d in json.loads(row["detections_json"])
            if d["label"] == "person" and float(d["confidence"]) >= CONF]

def in_roi(d):
    x1, y1, x2, y2 = d["box"]
    cx, cy = (x1+x2)/2, (y1+y2)/2
    return ROI[0] <= cx <= ROI[2] and ROI[1] <= cy <= ROI[3]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("detections", type=Path)
    ap.add_argument("output_dir", type=Path)
    args = ap.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    rows=[]
    with args.detections.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ds=[d for d in persons(row) if in_roi(d)]
            dt=datetime.fromisoformat(row["event_local"])
            shift="白班" if 8 <= dt.hour < 20 else "夜班"
            rows.append({"event_local":row["event_local"],"shift":shift,
                         "seat_visible_count":len(ds),"seat_visible":bool(ds),
                         "image":row["image"]})
    rows.sort(key=lambda r:r["event_local"])
    with (args.output_dir/"timeline.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    runs=[]; current=[]; previous=None
    for r in rows:
        dt=datetime.fromisoformat(r["event_local"])
        if not r["seat_visible"]:
            if len(current)>=3: runs.append(current)
            current=[]; previous=None; continue
        if previous is None or (dt-previous).total_seconds() <= 11*60 and r["shift"] == current[0]["shift"]:
            current.append(r)
        else:
            if len(current)>=3: runs.append(current)
            current=[r]
        previous=dt
    if len(current)>=3: runs.append(current)
    out=[]
    for i,run in enumerate(runs,1):
        start=datetime.fromisoformat(run[0]["event_local"]); end=datetime.fromisoformat(run[-1]["event_local"])
        out.append({"id":f"seat-{i}","shift":run[0]["shift"],"start":run[0]["event_local"],"last_sample":run[-1]["event_local"],"observed_minutes":int((end-start).total_seconds()/60),"samples":len(run),"images":";".join(r["image"] for r in run)})
    with (args.output_dir/"over10min_candidates.csv").open("w",encoding="utf-8",newline="") as f:
        fields=["id","shift","start","last_sample","observed_minutes","samples","images"]; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
    summary={"sample_interval_minutes":10,"roi_960px":ROI,"confidence":CONF,
             "criterion":"same computer-seat zone visible on at least 3 consecutive 10-minute samples (observed span >10 minutes)",
             "candidates":out}
    (args.output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
if __name__ == "__main__": main()
