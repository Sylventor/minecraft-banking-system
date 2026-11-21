"""Create a new local-only development environment without printing credentials."""

from pathlib import Path
import secrets


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    template = (root / ".env.example").read_text(encoding="utf-8")
    values = {
        name: secrets.token_hex(32)
        for name in (
            "POSTGRES_SUPERUSER_PASSWORD",
            "FINANCE_DB_PASSWORD",
            "AUTH_DB_PASSWORD",
            "INTERNAL_GRPC_TOKEN",
            "API_KEY_PEPPER",
            "DISCORD_BOT_INTERNAL_TOKEN",
        )
    }
    values.update(
        {
            "ENVIRONMENT": "development",
            "PUBLIC_ORIGIN": "http://localhost:8080",
            "CORS_ORIGINS": "http://localhost:8080",
            "DISCORD_REDIRECT_URI": "",
        }
    )
    lines = []
    for line in template.splitlines():
        key, separator, _ = line.partition("=")
        lines.append(key + "=" + values[key] if separator and key in values else line)
    # Compose constructs the actual application URLs from its separate passwords.
    lines = [
        line
        for line in lines
        if not line.startswith(("FINANCE_DATABASE_URL=", "AUTH_DATABASE_URL="))
    ]
    try:
        with target.open("x", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
        target.chmod(0o600)
    except FileExistsError:
        raise SystemExit(
            "Refusing to overwrite an existing .env; review it manually."
        ) from None
    print("Created an ignored local development .env with independent random secrets.")


if __name__ == "__main__":
    main()
