"""Fail on likely committed secrets without echoing secret contents."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = (
    re.compile(r"hbk_[A-Za-z0-9]{12}_[A-Za-z0-9_-]{40,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}"),
)


def scan() -> int:
    paths = subprocess.run(
        [
            "rg",
            "--files",
            "--hidden",
            "-g",
            "!.git/**",
            "-g",
            "!.tools/**",
            "-g",
            "!.venv/**",
            "-g",
            "!build/**",
            "-g",
            "!**/__pycache__/**",
            "-g",
            "!**/.mypy_cache/**",
            "-g",
            "!**/node_modules/**",
            "-g",
            "!**/generated/**",
            "-g",
            "!**/dist/**",
            "-g",
            "!.env",
            "-g",
            "!.env.*",
            "-g",
            "!**/pnpm-lock.yaml",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    findings = []
    for name in paths:
        path = ROOT / name
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                findings.append((name, number))
    for name, number in findings:
        print(f"Potential secret: {name}:{number} [redacted]")
    if not findings:
        print("Source secret scan passed")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(scan())
