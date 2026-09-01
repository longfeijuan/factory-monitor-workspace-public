# Camera calibration

Read this only when adding a camera or when the configured signal no longer separates.

1. Confirm the camera ID, machine name, and physical machine with the user.
2. Extract a few local frames known to show running and stopped states.
3. Draw a tight ROI around the fixed status signal. Never use the whole frame.
4. Measure the configured color features on both state sets.
5. Select a threshold with clear separation and record reference resolution, ROI, detector values, sampling interval, validation date, and `status: validated` in `cameras.json`.
6. Run a short window and visually inspect one running and one stopped evidence frame.
7. If signal states overlap, keep the camera unconfigured and return `machine_runtime_signal_not_separated`.

Do not infer running from personnel presence, machine doors, ambient light, or nearby machines unless a separate validated detector is explicitly configured.
