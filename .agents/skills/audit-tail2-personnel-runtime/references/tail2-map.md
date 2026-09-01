# Tail-2 camera and responsibility map

## Cameras

| Recorder | Channel | View | Required use |
|---|---:|---|---|
| `nvr-main-02` | 57 | Tail-2 overview | See the central four-machine area and much of the left/back area; use for cross-camera personnel confirmation. |
| `nvr-main-02` | 58 | Alternate Tail-2 overview | Primary view of 刘凡富's two-machine area; large machines create blind spots, so never use alone for absence. |

## Personnel and machines

- 罗明金: responsible for the four machines numbered 1–4 in channel 57.
- 刘凡富: responsible for the two machines numbered 1–2 in channel 58.
- Historical personnel images are intentionally not stored in Git. On every date, use a same-day user label or a continuous cross-camera path before attaching a name; otherwise report an unidentified visible person.

## Lamp regions

The values below are approximate fractions of the full current frame and are only for locating the lamp before same-day visual confirmation:

| View | Machine | Approximate lamp region `(x1,y1,x2,y2)` |
|---|---:|---|
| channel 57 | 1 | `(0.57, 0.59, 0.65, 0.70)` |
| channel 57 | 2 | `(0.35, 0.25, 0.40, 0.31)` |
| channel 57 | 3 | `(0.06, 0.27, 0.10, 0.33)` |
| channel 57 | 4 | `(0.01, 0.59, 0.05, 0.66)` |
| channel 58 | 1 | `(0.09, 0.40, 0.24, 0.52)` |
| channel 58 | 2 | `(0.32, 0.38, 0.47, 0.57)` |

If a lamp does not visibly fall inside the expected region, stop automatic classification and mark the mapping as needing local recalibration. Do not reconstruct or commit the historical annotated frames.

## Fixed Tail-2 schedule

- Work: `08:00–12:00`
- Break: `12:00–13:30`
- Work: `13:30–17:30`
- Break: `17:30–18:00`
- Work: `18:00–21:00`
- Effective work time: 11 hours
- No night shift unless the user says otherwise.

## Known failure modes

1. Channel 58 alone can hide 刘凡富 behind a large machine while channel 57 still shows him.
2. Low or zero green-light runtime does not prove that the operator left the area.
3. A person at 罗明金's central machines must not be relabeled as 刘凡富 merely because that person is closer to channel 58.
4. The NVR snapshot helper may request playback beginning one minute before the target and decode the first frame. Always read the on-screen timestamp rather than the episode folder name.
5. Bright white/yellow tower lights can be mistaken for green. Require visible green hue or green spill on nearby surfaces.

## Existing workspace helpers

When the `lost-item-investigator` workspace is available, prefer its existing scripts instead of rewriting extraction logic:

- `scripts/generate_tail2_attendance_samples.py`: generate channel 57/58 sample requests.
- `scripts/nvr_h264_snapshots.py`: read NVR playback and save evidence frames.

Check each script's current arguments before running it. Treat failures in its summary as unknown coverage and retry with lower concurrency before concluding.

## Output table

| Person | Machine mapping | Green/valid samples | Runtime rate | Approx. machine-hours |
|---|---|---:|---:|---:|
| 罗明金 | Channel 57 machines 1–4 | per machine and total | percentage | hours |
| 刘凡富 | Channel 58 machines 1–2 | per machine and total | percentage | hours |

For personnel findings, use: person, confirmed time, visible action, both-camera check, classification, and evidence paths.
