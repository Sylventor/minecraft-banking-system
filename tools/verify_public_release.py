"""Verify publication boundaries; never echo suspected secret contents."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {
    ".git",
    "node_modules",
    ".venv",
    ".tools",
    "build",
    "dist",
    "__pycache__",
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}"),
    re.compile(r"hbk_[A-Za-z0-9]{12}_[A-Za-z0-9_-]{40,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--initial",
        action="store_true",
        help="Require exactly one local initial commit",
    )
    args = parser.parse_args()
    git = ["git", "-c", "safe.directory=" + ROOT.as_posix(), "-C", str(ROOT)]
    paths: list[Path] = []
    if (ROOT / ".git").exists():
        names = subprocess.check_output([*git, "ls-files", "-z"]).decode().split("\0")
        paths = [ROOT / name for name in names if name]
    if not paths:
        paths = [
            p
            for p in ROOT.rglob("*")
            if p.is_file() and not SKIP.intersection(p.relative_to(ROOT).parts)
        ]
    failures = []
    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        if (
            (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower()
            in {
                ".log",
                ".dump",
                ".backup",
                ".sqlite",
                ".sqlite3",
                ".pem",
                ".key",
                ".p12",
            }
            or "SECURITY_AUDIT" in path.name
            or SKIP.intersection(Path(name).parts)
        ):
            failures.append(name + ": forbidden publication path")
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                failures.append(f"{name}:{line_no}: possible secret [redacted]")
        is_ui_legal = name.startswith("frontend/src/content/legal/")
        if (
            path.suffix == ".md"
            and not is_ui_legal
            and re.search(r"[\u0400-\u04ff]", content)
        ):
            failures.append(name + ": public documentation must be English")
        if (name.startswith("docs/") or path.name == "README.md") and re.search(
            r"Horizon\s*Bank", content, re.I
        ):
            failures.append(name + ": legacy public branding")
    if (ROOT / ".git").exists():
        remotes = subprocess.check_output([*git, "remote", "-v"]).decode()
        if re.search(r"HorizonBank2", remotes, re.I):
            failures.append("Legacy production remote must not be present")
        if (
            args.initial
            and int(subprocess.check_output([*git, "rev-list", "--count", "--all"]))
            != 1
        ):
            failures.append("Expected exactly one new initial commit")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(
        f"Public boundary checks passed for {len(paths)} files; localized UI legal content is an intentional language exception."
    )


if __name__ == "__main__":
    main()
