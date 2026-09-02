#!/usr/bin/env python3
"""Fail when a public release contains likely internal addresses or secrets."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", "dist", "__pycache__", "tests"}
SKIP_FILES = {Path(__file__).name}
ALLOWED_PRIVATE_IPV4 = {"192.168.0.1", "192.168.0.2", "192.168.0.3", "192.168.88.254"}
PATTERNS = {
    "private IPv4 address": re.compile(
        r"(?<!\d)(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\d)"
    ),
    "credentials embedded in URL": re.compile(r"://[^/\s:@]+:[^@\s/]+@"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "hard-coded secret assignment": re.compile(
        r"(?i)\b(?:password|passwd|token|secret)\s*=\s*(['\"])[^'\"\n]+\1"
    ),
}


def main() -> int:
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.name in SKIP_FILES or any(part in SKIP_PARTS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                if label == "private IPv4 address" and match.group(0) in ALLOWED_PRIVATE_IPV4:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(ROOT)}:{line}: {label}")
    if findings:
        print("公开安全检查失败：", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("公开安全检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
