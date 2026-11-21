"""Regression for source files copied from a checkout created with umask 077."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("dockerfile", "normalizer"),
    [
        ("backend/Dockerfile", "chmod -R u=rwX,go=rX /app /proto"),
        ("bot/Dockerfile", "chmod -R u=rwX,go=rX /app"),
        ("atm/Dockerfile", "chmod -R u=rwX,go=rX /app"),
        ("database/finance/Dockerfile", "chmod -R u=rwX,go=rX /work"),
        ("frontend/Dockerfile", "chmod -R u=rwX,go=rX /usr/share/nginx/html"),
    ],
)
def test_runtime_source_modes_are_normalized_after_copy(dockerfile, normalizer):
    source = (ROOT / dockerfile).read_text(encoding="utf-8")
    assert source.rfind("COPY ") < source.index(normalizer)
    if "USER " in source:
        assert source.index(normalizer) < source.index("USER ")
    assert "--chown=10001" not in source


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod semantics require Linux/macOS")
def test_private_sources_become_readable_but_not_writable_to_service(tmp_path):
    directory = tmp_path / "app"
    directory.mkdir(mode=0o700)
    module = directory / "models.py"
    module.write_text("# source\n", encoding="utf-8")
    module.chmod(0o600)
    subprocess.run(["chmod", "-R", "u=rwX,go=rX", str(directory)], check=True)
    assert directory.stat().st_mode & 0o777 == 0o755
    assert module.stat().st_mode & 0o777 == 0o644
