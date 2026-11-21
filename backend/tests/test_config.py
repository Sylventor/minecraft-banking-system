import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize("failure", ["short_service_token", "reused_verifier_token"])
def test_configuration_errors_never_print_secret_inputs(failure: str) -> None:
    token = "private-test-service-" + "s" * 40
    values = {
        "environment": "test",
        "auth_database_url": "postgresql+asyncpg://private_user:private_password@localhost/test",
        "internal_grpc_token": token,
        "api_key_pepper": "private-test-pepper-" + "p" * 40,
        "discord_bot_internal_token": "private-test-bot-" + "b" * 40,
        "minecraft_verifier_token": token if failure == "reused_verifier_token" else "",
    }
    if failure == "short_service_token":
        values["internal_grpc_token"] = "short-private-secret"
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **values)
    rendered = str(error.value)
    assert "private_password" not in rendered
    assert token not in rendered
    assert "short-private-secret" not in rendered


def production_settings(**overrides: str) -> Settings:
    values = {
        "environment": "production",
        "auth_database_url": "postgresql+asyncpg://demo@localhost/demo",
        "internal_grpc_token": "a" * 48,
        "api_key_pepper": "b" * 48,
        "discord_bot_internal_token": "c" * 48,
        "public_origin": "https://bank.example.com",
        "cors_origins": "https://bank.example.com",
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.mark.parametrize("environment", ["prod", "Production", "staging", ""])
def test_unknown_environment_cannot_silently_disable_production_controls(environment: str) -> None:
    with pytest.raises(ValidationError):
        production_settings(environment=environment)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.example.com",
        "null",
        "http://bank.example.com",
        "https://bank.example.com/path",
        "https://user:pass@bank.example.com",
    ],
)
def test_production_cors_rejects_wildcards_plaintext_and_non_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        production_settings(cors_origins=origin)


def test_production_accepts_explicit_https_origins_and_preserves_secure_mode() -> None:
    settings = production_settings(cors_origins="https://bank.example.com, https://dashboard.example.com:8443")
    assert settings.production
    assert settings.cors_origin_list == ["https://bank.example.com", "https://dashboard.example.com:8443"]
