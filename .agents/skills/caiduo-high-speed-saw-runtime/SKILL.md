---
name: caiduo-high-speed-saw-runtime
description: Read-only audit of Caiduo machine runtime from configured NVR camera status-lamp ROIs, producing running-rate, stopped periods, stop-to-run counts, unknown coverage, and a few evidence frames. Use when the user asks about 材多机台开机率、运行率、开机频率、启停次数、未开机时段、停机时段, especially channel 022 高速锯板机, or asks to continue a previous machine-runtime check without repeating an already analyzed time window.
---

# 材多高速锯板机开机率

Use the bundled local script to inspect configured machine status lamps. Keep NVR access read-only and keep media processing local.

## Workflow

1. Resolve the requested camera in `references/cameras.json`.
   - Accept `022`, `通道022`, or `cam-nvr-caiduo-022` as the same camera.
   - Use the configured machine name exactly. Channel 022 is `高速锯板机`.
   - If absent, stop with `machine_runtime_camera_not_configured`; never guess an ROI or use the whole frame.
2. Fix a timezone-aware Asia/Shanghai time window before reading media.
   - For “到现在”, capture the current time once and keep it fixed.
   - For “上午查过的不用查”, start exactly at the previous result's `window.end`.
   - Limit one run to 24 hours. Split longer requests into non-overlapping windows of at most 24 hours and aggregate only after each window passes QA.
3. Run:

```bash
python3 .agents/skills/caiduo-high-speed-saw-runtime/scripts/analyze_runtime.py \
  --camera-id 022 \
  --start '2026-08-12T10:51:46+08:00' \
  --end '2026-08-12T22:09:46+08:00' \
  --output-dir '/absolute/workspace/path/outputs/caiduo-runtime-022'
```

   - On Windows, append `--import-from-dingtalk`. The script must reuse `connector/gate_nvr_service.py`; the authorized `dws` session supplies the read-only credential in memory for that run.
   - On macOS, omit the flag after the connector has stored the credential in Keychain. It is still valid to use the flag for the first authorized import.
   - If `dws` is not on `PATH`, also pass `--dws 'C:\\absolute\\path\\to\\dws.exe'`.

4. Read `result.json` and `report.md`. Inspect no more than a few files from `evidence/` when manual confirmation is useful; do not send whole video or all frames to an LLM.
5. Report the exact window, running rate, running/stopped duration, stop-to-run count, major stopped intervals, sample interval, effective coverage, unknown duration, and ± one-sample boundary precision.

## Metric and QA rules

- Treat the configured green-lamp threshold as `running`; treat a clearly non-green/amber lamp as `stopped` only through the configured detector.
- Define `开机率/运行率` as running duration divided by valid classified duration. When unknown is zero, this equals the full-window rate.
- When the user says `开机频率`, report both running rate and the count of `stopped → running` transitions.
- Treat status-lamp runtime as machine-indicated running, not proof of spindle cutting, material throughput, or productive output.
- Retry failed frame reads locally. Never classify remaining unknown samples as running or stopped.
- Do not bridge a stopped interval across unknown samples.
- If unknown exceeds 2% or includes a contiguous gap of at least 5 minutes, label the result incomplete and present the script's lower/upper full-window bounds instead of a definitive full-window rate.
- Preserve one-minute sampling by default. Boundaries have approximately ± one sampling interval precision.
- Verify that `running_green_min` is above `stopped_green_max`. If separation fails, return `machine_runtime_signal_not_separated` and require ROI/threshold review.

## Safety

- Load NVR credentials only through the shared connector. On macOS it may use Keychain; on Windows it must use explicit `--import-from-dingtalk` and keep the credential only in the current process. Never print, log, copy, or embed credentials in reports or Git.
- Use RTSP/ISAPI read-only retrieval only. Do not deploy, restart services, change NVR settings, or modify formal configurations.
- Keep workers at 6 or fewer by default. Store only JSON, Markdown, and a few representative JPEGs in the requested output directory.
- For camera calibration or adding a camera, read `references/calibration.md`. Add a configuration entry only after visual confirmation; do not change core code.

## Outputs

- `result.json`: machine-readable metric, coverage, quality checks, and intervals.
- `report.md`: concise Chinese report.
- `samples.json`: per-sample metrics without images or credentials.
- `evidence/`: at most one representative running and one representative stopped frame by default.
