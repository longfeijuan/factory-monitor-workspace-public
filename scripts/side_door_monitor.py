#!/usr/bin/env python3
"""Continuously watch the No. 58 side door and notify on persistent anomalies.

The monitor is deliberately conservative: it requires several consecutive
abnormal samples before notifying, stores the triggering frame locally, and
never performs identity recognition.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from PIL import Image, ImageFilter, ImageOps, ImageStat

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
CONNECTOR_PATH = SCRIPT_ROOT / "connector" / "gate_nvr_service.py"


def acquire_nonblocking_file_lock(handle: Any) -> None:
    """Hold a one-byte process lock for the lifetime of an open file handle."""
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    if msvcrt is not None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError("monitor lock is already held") from error
        return
    raise RuntimeError("当前系统不支持进程文件锁。")


def load_connector() -> Any:
    spec = importlib.util.spec_from_file_location("gate_nvr_service", CONNECTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载NVR连接器。")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class MonitorConfig:
    path: Path
    values: dict[str, Any]

    @classmethod
    def read(cls, path: Path) -> "MonitorConfig":
        return cls(path.resolve(), json.loads(path.read_text(encoding="utf-8")))

    def resolve_path(self, key: str) -> Path:
        raw = Path(str(self.values[key]))
        return raw if raw.is_absolute() else (self.path.parent / raw).resolve()


@dataclass(frozen=True)
class DoorAnalysis:
    state: str
    reason: str
    baseline_diff: float
    center_contrast: float
    center_bright_fraction: float
    bottom_contrast: float
    bottom_bright_run: float
    frame_mean: float
    frame_stddev: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "baselineDiff": round(self.baseline_diff, 5),
            "centerContrast": round(self.center_contrast, 3),
            "centerBrightFraction": round(self.center_bright_fraction, 5),
            "bottomContrast": round(self.bottom_contrast, 3),
            "bottomBrightRun": round(self.bottom_bright_run, 5),
            "frameMean": round(self.frame_mean, 3),
            "frameStddev": round(self.frame_stddev, 3),
        }


def normalized_crop(image: Image.Image, roi: list[float]) -> Image.Image:
    width, height = image.size
    x1, y1, x2, y2 = roi
    return image.crop(
        (
            max(0, round(x1 * width)),
            max(0, round(y1 * height)),
            min(width, round(x2 * width)),
            min(height, round(y2 * height)),
        )
    )


def pixel_values(image: Image.Image) -> list[int]:
    if hasattr(image, "get_flattened_data"):
        return list(image.get_flattened_data())
    return list(image.getdata())


def normalized_difference(current: Image.Image, baseline: Image.Image, roi: list[float]) -> float:
    size = (160, 160)
    current_roi = normalized_crop(current, roi).convert("L").resize(size, Image.Resampling.LANCZOS)
    baseline_roi = normalized_crop(baseline, roi).convert("L").resize(size, Image.Resampling.LANCZOS)
    current_roi = ImageOps.autocontrast(current_roi).filter(ImageFilter.GaussianBlur(2.0))
    baseline_roi = ImageOps.autocontrast(baseline_roi).filter(ImageFilter.GaussianBlur(2.0))
    current_values = pixel_values(current_roi)
    baseline_values = pixel_values(baseline_roi)
    return fmean(abs(left - right) for left, right in zip(current_values, baseline_values)) / 255.0


def center_gap_metrics(gray: Image.Image, config: dict[str, Any]) -> tuple[float, float]:
    roi = config["center_seam_roi"]
    center = normalized_crop(gray, roi)
    x1, y1, x2, y2 = roi
    width = x2 - x1
    left = normalized_crop(gray, [x1 - width * 1.7, y1, x1 - width * 0.35, y2])
    right = normalized_crop(gray, [x2 + width * 0.35, y1, x2 + width * 1.7, y2])
    center_values = pixel_values(center)
    surround_values = pixel_values(left) + pixel_values(right)
    center_mean = fmean(center_values) if center_values else 0.0
    surround_mean = fmean(surround_values) if surround_values else 0.0
    threshold = max(210.0, surround_mean + 45.0)
    bright_fraction = (
        sum(value >= threshold for value in center_values) / len(center_values)
        if center_values
        else 0.0
    )
    return center_mean - surround_mean, bright_fraction


def longest_true_run(values: list[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def bottom_gap_metrics(gray: Image.Image, config: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = config["bottom_gap_roi"]
    gap = normalized_crop(gray, [x1, y1, x2, y2])
    band_height = y2 - y1
    above = normalized_crop(gray, [x1, max(0.0, y1 - band_height * 1.25), x2, y1])
    sample_width = min(gap.width, above.width)
    if sample_width <= 0:
        return 0.0, 0.0
    gap = gap.resize((sample_width, max(1, gap.height)), Image.Resampling.BILINEAR)
    above = above.resize((sample_width, max(1, above.height)), Image.Resampling.BILINEAR)
    column_contrasts: list[float] = []
    bright_columns: list[bool] = []
    for x in range(sample_width):
        gap_values = [gap.getpixel((x, y)) for y in range(gap.height)]
        above_values = [above.getpixel((x, y)) for y in range(above.height)]
        peak = max(gap_values)
        contrast = peak - fmean(above_values)
        column_contrasts.append(contrast)
        bright_columns.append(peak >= 225 and contrast >= 40)
    positive = [value for value in column_contrasts if value > 0]
    contrast = fmean(positive) if positive else 0.0
    run_fraction = longest_true_run(bright_columns) / sample_width
    return contrast, run_fraction


def analyze_door(image: Image.Image, baseline: Image.Image, config: dict[str, Any]) -> DoorAnalysis:
    rgb = image.convert("RGB")
    gray = rgb.convert("L")
    frame_stats = ImageStat.Stat(gray.resize((96, 96), Image.Resampling.BILINEAR))
    mean = frame_stats.mean[0]
    stddev = frame_stats.stddev[0]
    if mean < float(config["thresholds"]["min_frame_mean"]) or stddev < float(
        config["thresholds"]["min_frame_stddev"]
    ):
        return DoorAnalysis("unavailable", "画面全黑、遮挡或无有效细节", 0, 0, 0, 0, 0, mean, stddev)

    baseline_diff = normalized_difference(rgb, baseline, config["door_face_roi"])
    center_contrast, center_bright = center_gap_metrics(gray, config)
    bottom_contrast, bottom_run = bottom_gap_metrics(gray, config)
    thresholds = config["thresholds"]

    open_detected = baseline_diff >= float(thresholds["open_baseline_diff"])
    center_ajar = center_contrast >= float(thresholds["center_contrast"]) and center_bright >= float(
        thresholds["center_bright_fraction"]
    )
    bottom_ajar = bool(config.get("use_bottom_gap", True)) and bottom_contrast >= float(
        thresholds["bottom_contrast"]
    ) and bottom_run >= float(thresholds["bottom_bright_run"])
    if center_ajar:
        state, reason = "ajar", "门中缝持续透光"
    elif bottom_ajar:
        state, reason = "ajar", "底部门缝持续透光"
    elif open_detected:
        state, reason = "open", "门板位置与关门基准明显不同"
    else:
        state, reason = "closed", "门板位置和门缝均在关门基准内"
    return DoorAnalysis(
        state,
        reason,
        baseline_diff,
        center_contrast,
        center_bright,
        bottom_contrast,
        bottom_run,
        mean,
        stddev,
    )


class DingTalkNotifier:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def send(self, title: str, text: str, event_id: str) -> dict[str, Any]:
        mode = str(self.config.get("mode", "chat"))
        dws = str(self.config.get("dws", "dws"))
        if mode == "ding":
            robot_code = str(self.config.get("robot_code") or os.getenv("DINGTALK_DING_ROBOT_CODE", ""))
            if not robot_code:
                raise RuntimeError("DING模式缺少robot_code。")
            command = [
                dws,
                "ding",
                "message",
                "send",
                "--robot-code",
                robot_code,
                "--type",
                str(self.config.get("ding_type", "app")),
                "--users",
                str(self.config["receiver_user_id"]),
                "--content",
                f"{title}\n{text}",
                "--format",
                "json",
            ]
        else:
            command = [
                dws,
                "chat",
                "message",
                "send",
                "--user",
                str(self.config["receiver_user_id"]),
                "--title",
                title,
                "--text",
                text,
                "--uuid",
                event_id,
                "--format",
                "json",
            ]
        if bool(self.config.get("dry_run", False)):
            command.insert(-2, "--dry-run")
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=45)
        return json.loads(completed.stdout)


class SideDoorMonitor:
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.values = config.values
        self.runtime_dir = config.resolve_path("runtime_dir")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir = self.runtime_dir / "evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.events_path = self.runtime_dir / "events.jsonl"
        self.baseline = Image.open(config.resolve_path("baseline_path")).convert("RGB")
        connector = load_connector()
        credentials, _ = connector.load_credentials(False, str(self.values.get("dws", "dws")))
        recorder = str(self.values["recorder"])
        self.nvr = connector.HikvisionNvr(recorder, credentials[recorder], timeout=8)
        self.notifier = DingTalkNotifier(self.values["notifier"])

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def append_event(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def fetch_snapshot(self) -> bytes:
        channel = int(self.values["channel"])
        return self.nvr.request(f"/ISAPI/Streaming/channels/{channel * 100 + 1}/picture")

    def save_evidence(self, image_bytes: bytes, now: datetime, label: str) -> Path:
        destination = self.evidence_dir / f"{now:%Y%m%d-%H%M%S}-{label}.jpg"
        destination.write_bytes(image_bytes)
        retention_days = int(self.values.get("evidence_retention_days", 7))
        cutoff = time.time() - retention_days * 86400
        for path in self.evidence_dir.glob("*.jpg"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        return destination

    def send_alert(
        self,
        now: datetime,
        analysis: DoorAnalysis,
        duration_seconds: int,
        evidence: Path,
        event_id: str,
    ) -> dict[str, Any]:
        location = str(self.values["location"])
        title = f"【{location}异常】请立即检查"
        text = (
            f"时间：{now:%Y-%m-%d %H:%M:%S}\n"
            f"状态：{analysis.state}（{analysis.reason}）\n"
            f"已持续：约{duration_seconds}秒\n"
            f"证据已保存在监控电脑：{evidence}\n"
            "请到现场确认门是否关紧。"
        )
        return self.notifier.send(title, text, event_id)

    def send_recovery(self, now: datetime, event_id: str) -> dict[str, Any]:
        location = str(self.values["location"])
        return self.notifier.send(
            f"【{location}恢复】",
            f"时间：{now:%Y-%m-%d %H:%M:%S}\n侧门画面已连续恢复到关门基准。",
            event_id,
        )

    def run_once(self) -> dict[str, Any]:
        now = datetime.now().astimezone()
        now_epoch = now.timestamp()
        state = self.read_state()
        image_bytes = b""
        frame_hash = ""
        try:
            image_bytes = self.fetch_snapshot()
            frame_hash = hashlib.sha256(image_bytes).hexdigest()
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            analysis = analyze_door(image, self.baseline, self.values["detector"])
            if state.get("lastFrameHash") == frame_hash:
                state["sameFrameCount"] = int(state.get("sameFrameCount", 0)) + 1
            else:
                state["sameFrameCount"] = 0
            if int(state.get("sameFrameCount", 0)) >= int(self.values.get("frozen_frame_samples", 3)):
                analysis = DoorAnalysis(
                    "unavailable",
                    "实时截图连续完全相同，疑似画面冻结",
                    analysis.baseline_diff,
                    analysis.center_contrast,
                    analysis.center_bright_fraction,
                    analysis.bottom_contrast,
                    analysis.bottom_bright_run,
                    analysis.frame_mean,
                    analysis.frame_stddev,
                )
        except Exception as error:
            analysis = DoorAnalysis("unavailable", f"监控读取失败：{error}", 0, 0, 0, 0, 0, 0, 0)

        abnormal = analysis.state in {"ajar", "open", "unavailable"}
        pending_state = str(state.get("pendingState", ""))
        if abnormal:
            if pending_state != analysis.state:
                state["pendingState"] = analysis.state
                state["pendingSince"] = now_epoch
            duration = max(0, int(now_epoch - float(state.get("pendingSince", now_epoch))))
            persist_seconds = int(
                self.values.get(
                    "unavailable_persist_seconds" if analysis.state == "unavailable" else "persist_seconds",
                    60,
                )
            )
            cooldown_seconds = int(self.values.get("cooldown_seconds", 1800))
            last_alert_at = float(state.get("lastAlertAt", 0))
            should_alert = duration >= persist_seconds and (
                not state.get("activeAlert")
                or (
                    state.get("lastNotificationStatus") == "failed"
                    and now_epoch - last_alert_at >= int(self.values.get("notification_retry_seconds", 60))
                )
                or now_epoch - last_alert_at >= cooldown_seconds
            )
            if should_alert:
                evidence = self.save_evidence(image_bytes, now, analysis.state) if image_bytes else self.runtime_dir
                event_id = f"side-door-{now:%Y%m%d%H%M}-{analysis.state}"
                try:
                    notification = self.send_alert(now, analysis, duration, evidence, event_id)
                    notification_status = "sent"
                except Exception as error:
                    notification = {"error": str(error)}
                    notification_status = "failed"
                state.update(
                    {
                        "activeAlert": True,
                        "activeAlertState": analysis.state,
                        "lastAlertAt": now_epoch,
                        "lastNotificationStatus": notification_status,
                        "lastNotification": notification,
                    }
                )
                self.append_event(
                    {
                        "time": now.isoformat(),
                        "type": "alert",
                        "analysis": analysis.as_dict(),
                        "durationSeconds": duration,
                        "evidence": str(evidence),
                        "notificationStatus": notification_status,
                    }
                )
        else:
            state.pop("pendingState", None)
            state.pop("pendingSince", None)
            if state.get("activeAlert"):
                recovery_since = float(state.get("recoverySince", now_epoch))
                state.setdefault("recoverySince", recovery_since)
                recovery_duration = now_epoch - recovery_since
                if recovery_duration >= int(self.values.get("recovery_seconds", 60)):
                    event_id = f"side-door-{now:%Y%m%d%H%M}-recovery"
                    try:
                        notification = self.send_recovery(now, event_id)
                        notification_status = "sent"
                    except Exception as error:
                        notification = {"error": str(error)}
                        notification_status = "failed"
                    self.append_event(
                        {
                            "time": now.isoformat(),
                            "type": "recovery",
                            "notificationStatus": notification_status,
                        }
                    )
                    state.update(
                        {
                            "activeAlert": notification_status != "sent",
                            "lastRecoveryAt": now_epoch if notification_status == "sent" else state.get("lastRecoveryAt"),
                            "lastNotificationStatus": notification_status,
                            "lastNotification": notification,
                        }
                    )
                    if notification_status == "sent":
                        state.pop("recoverySince", None)
            else:
                state.pop("recoverySince", None)

        state.update(
            {
                "updatedAt": now.isoformat(),
                "lastFrameHash": frame_hash,
                "lastAnalysis": analysis.as_dict(),
            }
        )
        self.write_state(state)
        result = {"time": now.isoformat(), "analysis": analysis.as_dict(), "state": state}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    def run_daemon(self) -> None:
        lock_path = self.runtime_dir / "monitor.lock"
        lock_path.touch(exist_ok=True)
        lock_handle = lock_path.open("r+", encoding="utf-8")
        try:
            acquire_nonblocking_file_lock(lock_handle)
        except BlockingIOError:
            print("已有58号侧门监控进程在运行。", file=sys.stderr, flush=True)
            return
        lock_handle.seek(0)
        lock_handle.write(str(os.getpid()))
        lock_handle.truncate()
        lock_handle.flush()
        stopped = False

        def stop_handler(_signum: int, _frame: Any) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)
        interval = max(5, int(self.values.get("poll_seconds", 15)))
        while not stopped:
            started = time.monotonic()
            self.run_once()
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()
    monitor = SideDoorMonitor(MonitorConfig.read(args.config))
    if args.daemon:
        monitor.run_daemon()
    else:
        monitor.run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
