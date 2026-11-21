from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    project_name: str = Field(default="Minecraft Banking System", min_length=1, max_length=80)
    environment: Literal["development", "test", "production"] = "development"
    auth_database_url: str
    grpc_target: str = "core:50051"
    internal_grpc_token: SecretStr
    api_key_pepper: SecretStr
    discord_bot_internal_token: SecretStr
    discord_client_id: str = ""
    discord_client_secret: SecretStr = SecretStr("")
    discord_redirect_uri: str = ""
    minecraft_verifier_token: SecretStr = SecretStr("")
    atm_service_token: SecretStr = SecretStr("")
    atm_coordinator_url: str = "http://atm:8090"
    atm_identity_mode: Literal["online-mode", "server-auth"] = "online-mode"
    public_origin: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    cors_origins: str = "http://localhost:8080"
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    session_ttl_seconds: int = Field(default=2592000, ge=60, le=2592000)
    session_absolute_ttl_seconds: int = Field(default=7776000, ge=3600, le=7776000)

    @model_validator(mode="after")
    def validate_secrets(self) -> Settings:
        for value in (
            self.internal_grpc_token,
            self.api_key_pepper,
            self.discord_bot_internal_token,
        ):
            raw = value.get_secret_value()
            if len(raw) < 32 or (self.production and ("replace" in raw.lower() or raw.startswith("test-"))):
                raise ValueError("service secrets must have at least 32 characters and cannot be placeholders")
        if self.production:
            if self.public_origin.scheme != "https":
                raise ValueError("production requires HTTPS")
            public = urlsplit(str(self.public_origin))
            if public.username or public.password or public.path not in {"", "/"} or public.query or public.fragment:
                raise ValueError("public origin must contain only a scheme, host and optional port")
            for origin in self.cors_origin_list:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or "*" in origin
                    or parsed.username
                    or parsed.password
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("production CORS origins must be explicit HTTPS origins")
            if (self.discord_client_id or self.discord_redirect_uri) and self.discord_redirect_uri != str(
                self.public_origin
            ).rstrip("/") + "/api/v1/auth/discord/callback":
                raise ValueError("Discord redirect URI must match the exact callback route")
        verifier = self.minecraft_verifier_token.get_secret_value()
        atm = self.atm_service_token.get_secret_value()
        if atm and (
            len(atm) < 32
            or (self.production and "replace" in atm.lower())
            or atm
            in {
                self.internal_grpc_token.get_secret_value(),
                self.api_key_pepper.get_secret_value(),
                self.discord_bot_internal_token.get_secret_value(),
                verifier,
            }
        ):
            raise ValueError("ATM token must be a separate high-entropy secret of at least 32 characters")
        if verifier:
            if len(verifier) < 32 or (self.production and "replace" in verifier.lower()):
                raise ValueError("Minecraft verifier token must be a separate high-entropy secret")
            if verifier in {
                self.internal_grpc_token.get_secret_value(),
                self.api_key_pepper.get_secret_value(),
                self.discord_bot_internal_token.get_secret_value(),
            }:
                raise ValueError("Minecraft verifier must not reuse another service secret")
        return self

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
