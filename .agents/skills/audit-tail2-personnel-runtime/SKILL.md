---
name: audit-tail2-personnel-runtime
description: Audit Tail-2 warehouse personnel attendance and six-machine green-light runtime by cross-checking NVR channels 57 and 58. Use when the user asks about 尾2/尾二仓、刘凡富、罗明金、两人是否在岗/离岗/看手机、六台机开机率/运行率/绿灯、两个摄像头交叉复核，or asks to redo a previous Tail-2 surveillance result.
---

# Audit Tail-2 Personnel Runtime

Read [references/tail2-map.md](references/tail2-map.md) before analyzing footage. Use its camera, personnel, machine, and asset mapping.

## Establish the audit scope

1. Resolve the calendar date explicitly. Treat “昨天/今天” using the current Asia/Shanghai date and write the resolved date in the result.
2. Use the user's requested hours. Otherwise use Tail-2's confirmed schedule: `08:00–12:00`, `13:30–17:30`, `18:00–21:00` (11 effective hours; no night shift).
3. Separate the two questions:
   - Personnel: visible presence, prolonged absence, or prolonged phone use.
   - Machines: green-light runtime.
4. Never infer personnel absence from low machine runtime.

## Identify people safely

1. Inspect both channel 57 and channel 58 for every candidate interval.
2. Prefer identity evidence in this order:
   - A user-labeled frame from the same day.
   - A continuous cross-camera path from a clear identity anchor.
   - Confirmed responsibility area and machine assignment.
   - Clothing only as a same-day supporting cue.
3. Do not carry shirt color across days. The 2026-08-14 gray/blue-versus-black example is historical evidence, not a permanent identity rule.
4. If identity is not clear, report “可见人员，身份无法确认”. Do not guess.

## Collect and refine evidence

1. Screen both cameras at no more than 2-minute intervals for personnel.
2. Refine every suspected absence or phone-use interval at 30-second intervals or by continuous playback.
3. Use the on-screen camera timestamp as authoritative. Episode names and requested playback times may be offset.
4. Retain at least three frames for every reported sustained anomaly: start, middle, and end.
5. Treat an NVR playback error as unknown coverage, not absence or stopped runtime.

## Classify personnel findings

1. Count a person as present when either camera clearly shows that person working at their assigned machines or moving within the Tail-2 work area.
2. Do not mark a single-camera blind spot as absence.
3. Use 20 minutes as the default prolonged-absence candidate threshold unless the user specifies another threshold. Confirm the entire interval using both cameras before reporting it.
4. Count phone use only when a phone is visually clear and use is continuous. Use 10 minutes as the default prolonged-phone threshold unless the user specifies otherwise.
5. Report short or unclear hand-held-object events separately as “待核实”; do not call them violations.
6. Describe only visible actions. Do not infer production intent, authorization, or cause from surveillance alone.

## Calculate machine runtime

1. Use the six lamp regions in `references/tail2-map.md`. Reconfirm them against the first frame because camera geometry can change.
2. Count only a clearly green tower light as running. Treat yellow, white, red, or dark as stopped.
3. Mark a covered, blown-out, blurred, or unresolvable lamp as unknown; exclude unknown samples from the rate denominator.
4. Prefer 2-minute sampling. If only 10-minute sampling is practical, label the result “10分钟抽样估算”.
5. Calculate per machine:

   `runtime rate = green samples / valid samples`

6. Calculate each person's aggregate rate using summed green samples divided by summed valid samples across their assigned machines. Do not average already-rounded percentages.
7. Report approximate machine-hours as `runtime rate × effective shift hours` per machine, then sum machine-hours for owner and area totals.
8. Keep machine rate and personnel findings in separate sections.

## Quality gates

Before giving a conclusion, verify all of the following:

- Both channels 57 and 58 were checked for personnel conclusions.
- Personnel identity is anchored rather than inferred from one shirt or one location.
- All six machine lamp mappings were reconfirmed against both current camera views.
- Breaks and unknown coverage are excluded from the effective denominator.
- Every timestamp shown to the user comes from the video overlay.
- A previous conflicting finding is explicitly withdrawn if the new cross-camera evidence disproves it.

## Report format

Lead with the corrected outcome, then provide:

1. Personnel findings for 刘凡富 and 罗明金, including confirmed anomalies and non-anomalous candidates.
2. Per-machine runtime table grouped as 罗明金四台 and 刘凡富两台.
3. Owner totals and six-machine total.
4. Sampling interval, unknown coverage, and limitations.
5. Evidence frames for each confirmed anomaly or disputed correction.
