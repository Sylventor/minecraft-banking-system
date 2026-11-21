"""Reproducible clean-database verification; never restores into or deletes an existing database."""

import os
import subprocess
import sys
from pathlib import Path

from integration.bootstrap import bootstrap

if __name__ == "__main__":
    config = bootstrap()
    env = {**os.environ, **config, "ENVIRONMENT": "test"}
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "pytest", *(sys.argv[1:] or ["tests/integration"]), "-q"],
            cwd=root,
            env=env,
        )
    )
