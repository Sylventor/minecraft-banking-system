"""Generate the checked-in OpenAPI schema from the actual routes without external connections."""

from __future__ import annotations
import json
import os
from pathlib import Path

for key in ("INTERNAL_GRPC_TOKEN", "API_KEY_PEPPER", "DISCORD_BOT_INTERNAL_TOKEN"):
    os.environ[key] = "documentation-only-" + "x" * 40
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_DATABASE_URL"] = (
    "postgresql+asyncpg://documentation@127.0.0.1/documentation"
)
os.environ["PUBLIC_ORIGIN"] = "https://bank.example.invalid"

from app.config import Settings  # noqa: E402

# Documentation generation must never read a developer's private .env.
Settings.model_config["env_file"] = None

from app.main import app  # noqa: E402 -- isolated documentation configuration precedes module initialization

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[2] / "docs/openapi.json"
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Exported {len(app.openapi()['paths'])} implemented API paths")
