#!/usr/bin/env python3
"""Offline-safe portability checks for the monitoring workspace."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "camera-data" / "current"


def verify_manifest() -> tuple[bool, str]:
    manifest = CURRENT / "MANIFEST.sha256"
    source = CURRENT / "SOURCE.json"
    if not manifest.is_file():
        return False, "缺少camera-data/current/MANIFEST.sha256"
    if not source.is_file():
        return False, "缺少camera-data/current/SOURCE.json"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(maxsplit=1)
        target = CURRENT / relative.removeprefix("./")
        if not target.is_file():
            return False, f"资料包缺少：{relative}"
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            return False, f"资料包校验失败：{relative}"
    metadata = json.loads(source.read_text(encoding="utf-8"))
    filename = str(metadata.get("filename", "未知文件"))
    create_time = str(metadata.get("create_time", "未知时间"))
    return True, f"仓库内置脱敏包校验通过：{filename}（发布于{create_time}）"


def catalog_status() -> tuple[bool, str]:
    path = ROOT / "public" / "data" / "cameras.json"
    if not path.is_file():
        return False, "缺少public/data/cameras.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    inventory = json.loads((CURRENT / "data" / "摄像头脱敏库存.json").read_text(encoding="utf-8"))
    expected = int(inventory["expected_channel_count"])
    if len(rows) != expected:
        return False, f"工作台目录为{len(rows)}路，当前资料包为{expected}路"
    return True, f"工作台目录数量正确：{expected}路"


def nvr_endpoint_catalog_status() -> tuple[bool, str]:
    path = ROOT / "config" / "nvr-endpoints.json"
    if not path.is_file():
        return False, "缺少config/nvr-endpoints.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        return False, "NVR内置地址目录版本不是1"
    recorders = payload.get("recorders", {})
    expected = {"nvr-main-01", "nvr-main-02", "nvr-main-03", "nvr-caiduo"}
    if set(recorders) != expected:
        return False, "NVR内置地址目录没有完整包含4台录像机"
    for recorder, entry in recorders.items():
        expected_group = "caiduo" if recorder == "nvr-caiduo" else "main"
        if entry.get("credential_group") != expected_group:
            return False, f"{recorder}的凭据分组不正确"
        host = str(entry.get("host", ""))
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False, f"{recorder}的内置地址格式无效"
        if address.version != 4 or not address.is_private:
            return False, f"{recorder}的内置地址不是公司私有IPv4地址"
    return True, "4台获批NVR地址已内置；普通使用者只需本机录入账号和密码"


def gate58_contract_status() -> tuple[bool, str]:
    config_path = ROOT / "config" / "gate58-people-crossing-v2.json"
    skill_path = ROOT / ".agents" / "skills" / "audit-gate58-people-crossing" / "SKILL.md"
    contract_path = ROOT / "scripts" / "gate58_review_contract.py"
    compare_path = ROOT / "scripts" / "compare_gate58_results.py"
    common_path = ROOT / "scripts" / "gate58_common.py"
    pending_path = ROOT / "scripts" / "gate58_pending_manifest.py"
    apply_pending_path = ROOT / "scripts" / "gate58_apply_pending_reviews.py"
    for target in (
        config_path,
        skill_path,
        contract_path,
        compare_path,
        common_path,
        pending_path,
        apply_pending_path,
    ):
        if not target.is_file():
            return False, f"58号门跨电脑契约缺少：{target.relative_to(ROOT)}"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("policy_version") != "gate58-people-crossing-v2":
        return False, "58号门规则版本不是gate58-people-crossing-v2"
    camera = config.get("camera", {})
    if (camera.get("recorder"), camera.get("channel"), camera.get("track")) != (
        "nvr-main-02",
        1,
        101,
    ):
        return False, "58号门固定映射不是nvr-main-02/channel1/track101"
    quality = config.get("quality_gate", {})
    if (
        quality.get("maximum_cross_computer_enter_difference"),
        quality.get("maximum_cross_computer_exit_difference"),
        quality.get("maximum_cross_computer_total_difference"),
    ) != (2, 2, 2):
        return False, "58号门跨电脑偏差目标不是进入/外出/合计各≤2"
    return True, "58号门v2单次提问收敛流程与跨电脑≤2人次验收已安装"


def reproducibility_contract_status() -> tuple[bool, str]:
    config_path = ROOT / "config" / "monitor-reproducibility-v1.json"
    required = (
        config_path,
        ROOT / "scripts" / "monitor_query_context.py",
        ROOT / "scripts" / "monitor_result_contract.py",
        ROOT / "scripts" / "compare_monitor_results.py",
    )
    for target in required:
        if not target.is_file():
            return False, f"全项目跨电脑契约缺少：{target.relative_to(ROOT)}"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("policy_version") != "monitor-project-reproducibility-v1":
        return False, "全项目跨电脑规则版本不正确"
    tolerance = config.get("default_tolerances", {})
    if tolerance != {"count": 2, "percentage_point": 1.0, "minutes": 5}:
        return False, "全项目默认偏差不是计数2、比率1.0个百分点、时长5分钟"
    return True, "全项目查询指纹、统一结果封装与跨电脑偏差验收已安装"


def cnc_floor1_runtime_contract_status() -> tuple[bool, str]:
    config_path = ROOT / "config" / "cnc-floor1-runtime-v3.json"
    required = (
        config_path,
        ROOT / ".agents" / "skills" / "cnc-floor1-runtime-audit" / "SKILL.md",
        ROOT
        / ".agents"
        / "skills"
        / "cnc-floor1-runtime-audit"
        / "scripts"
        / "collect_runtime.py",
        ROOT / "scripts" / "analyze_cnc_six_green.py",
        ROOT / "scripts" / "analyze_cnc_six_green_blink.py",
    )
    for target in required:
        if not target.is_file():
            return False, f"一楼电脑锣开机率契约缺少：{target.relative_to(ROOT)}"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("policy_version") != "cnc-floor1-green-blink-v3.0-dual-view":
        return False, "一楼电脑锣开机率规则不是cnc-floor1-green-blink-v3.0-dual-view"
    sources = config.get("sources", {})
    if set(sources) != {"passage49", "fisheye1"}:
        return False, "一楼电脑锣双通道来源不完整"
    expected_cameras = {
        "passage49": ("nvr-main-02", 49, 4901, [1280, 720]),
        "fisheye1": ("nvr-main-02", 2, 201, [2560, 1440]),
    }
    machine_sources: dict[str, str] = {}
    for source_id, expected in expected_cameras.items():
        source = sources[source_id]
        camera = source.get("camera", {})
        calibration = source.get("calibration", {})
        actual = (
            camera.get("recorder"),
            camera.get("channel"),
            camera.get("track"),
            calibration.get("reference_size"),
        )
        if actual != expected:
            return False, f"一楼电脑锣{source_id}固定映射不正确"
        rois = calibration.get("machine_rois", {})
        if any(not isinstance(roi, list) or len(roi) != 4 for roi in rois.values()):
            return False, f"一楼电脑锣{source_id}灯位坐标格式不正确"
        for machine in rois:
            if machine in machine_sources:
                return False, f"一楼电脑锣{machine}号机重复分配来源"
            machine_sources[machine] = source_id
    expected_machine_sources = {
        "1": "passage49",
        "2": "passage49",
        "3": "passage49",
        "4": "fisheye1",
        "5": "passage49",
        "6": "passage49",
    }
    if machine_sources != expected_machine_sources:
        return False, "一楼电脑锣不是49通道五台加鱼眼1补4号机的固定映射"
    if config.get("machine_source_map") != expected_machine_sources:
        return False, "一楼电脑锣机台来源声明与灯位不一致"
    tolerance = config.get("quality_gate", {}).get(
        "maximum_cross_computer_rate_difference_pp"
    )
    if tolerance != 1.0:
        return False, "一楼电脑锣跨电脑比率偏差目标不是≤1.0个百分点"
    sampling = config.get("sampling", {})
    if (
        sampling.get("ambiguous_review_window_seconds"),
        sampling.get("ambiguous_review_minimum_span_seconds"),
        sampling.get("strong_single_frame_green_pixels"),
    ) != (20.0, 19.0, 12):
        return False, "一楼电脑锣临界绿帧规则不是20秒/至少19秒/强单帧12像素"
    return True, "一楼电脑锣v3双通道固定映射、鱼眼补4号机、临界点延长复核及跨电脑≤1.0个百分点契约已安装"


def windows_credential_onboarding_status() -> tuple[bool, str]:
    connector = ROOT / "connector" / "gate_nvr_service.py"
    setup = ROOT / "SETUP-NVR-CREDENTIALS.cmd"
    installer = ROOT / "scripts" / "install-windows.ps1"
    for target in (connector, setup, installer):
        if not target.is_file():
            return False, f"Windows本机凭据入口缺少：{target.relative_to(ROOT)}"
    connector_text = connector.read_text(encoding="utf-8")
    installer_text = installer.read_text(encoding="utf-8")
    required_connector_tokens = (
        "--setup-credentials",
        "--credential-status",
        "windows-credential-manager",
        "load_builtin_hosts",
    )
    if any(token not in connector_text for token in required_connector_tokens):
        return False, "Windows连接器未完整接入本机凭据管理器入口"
    if "Secure local NVR credential setup" not in installer_text:
        return False, "Windows一键安装器未接入本机NVR凭据向导"
    return True, "Windows普通使用者无需输入地址，可在不访问内部源群的情况下安全录入只读账号和密码"


def git_boundary_status() -> tuple[bool, str]:
    if not (ROOT / ".git").exists():
        return True, "当前是导出目录，跳过Git跟踪边界检查"
    completed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=20
    )
    forbidden = (
        "audit-output/",
        "output/",
        "outputs/",
        "tmp/",
        "runtime/",
        "secure-state/",
        "camera-data/private/",
    )
    bad = [line for line in completed.stdout.splitlines() if line.startswith(forbidden)]
    if bad:
        return False, "Git错误跟踪了本机运行资料：" + ", ".join(bad[:5])
    return True, "Git未跟踪录像、截图、运行结果或私密状态目录"


def main() -> int:
    parser = argparse.ArgumentParser(description="监控项目跨电脑自检")
    parser.add_argument("--live", action="store_true", help="同时要求本机已安全保存完整NVR只读连接项")
    parser.add_argument("--source-sync", action="store_true", help="资料同步人额外检查钉钉CLI")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks: list[dict[str, object]] = []
    for name, check in (
        ("sanitized_package", verify_manifest),
        ("generated_catalog", catalog_status),
        ("nvr_endpoint_catalog", nvr_endpoint_catalog_status),
        ("reproducibility_contract", reproducibility_contract_status),
        ("gate58_contract", gate58_contract_status),
        ("cnc_floor1_runtime_contract", cnc_floor1_runtime_contract_status),
        ("windows_credential_onboarding", windows_credential_onboarding_status),
        ("git_data_boundary", git_boundary_status),
    ):
        try:
            ok, message = check()
        except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as error:
            ok, message = False, f"{error.__class__.__name__}: {error}"
        checks.append({"name": name, "ok": ok, "required": True, "message": message})

    python_executable = shutil.which("python3") or shutil.which("python") or sys.executable
    tools = {
        "python": python_executable,
        "node": shutil.which("node"),
        "pnpm": shutil.which("pnpm"),
        "dws": shutil.which("dws") or str(Path.home() / ".local" / "bin" / "dws"),
    }
    for name, value in tools.items():
        exists = bool(value and Path(value).exists())
        required = name in {"python", "node", "pnpm"} or (args.source_sync and name == "dws")
        checks.append(
            {
                "name": f"tool_{name}",
                "ok": exists,
                "required": required,
                "message": f"{name}: {value if exists else '未找到'}",
            }
        )

    if args.live:
        connector = ROOT / "connector" / "gate_nvr_service.py"
        completed = subprocess.run(
            [sys.executable, str(connector), "--credential-status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        live_message = (completed.stdout or completed.stderr).strip()
        checks.append(
            {
                "name": "credential_store",
                "ok": completed.returncode == 0,
                "required": True,
                "message": live_message or "本机NVR连接项状态未知",
            }
        )

    failed = [item for item in checks if item["required"] and not item["ok"]]
    payload = {"ok": not failed, "project": str(ROOT), "checks": checks}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            symbol = "✓" if item["ok"] else ("✗" if item["required"] else "!")
            print(f"{symbol} {item['message']}")
        print("自检通过" if not failed else "自检未通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
