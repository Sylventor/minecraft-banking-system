# Configuration

Copy `.env.example` to an ignored `.env`, or use `tools/create_dev_env.py` for a new local development configuration. Never commit actual environment files.

| Setting | Purpose |
| --- | --- |
| `ENVIRONMENT` | Exactly `development`, `test`, or `production`; production requires HTTPS |
| `PUBLIC_ORIGIN` | Browser origin, also used to validate cookie-authenticated writes |
| `CORS_ORIGINS` | Comma-separated explicit allowed origins; production requires HTTPS origins |
| `POSTGRES_DB` | Database name for Compose initialization |
| `POSTGRES_SUPERUSER_PASSWORD` | Bootstrap administrator password; not an application credential |
| `FINANCE_DB_PASSWORD`, `AUTH_DB_PASSWORD` | Separate restricted PostgreSQL roles |
| `INTERNAL_GRPC_TOKEN` | Private API-to-core authentication |
| `API_KEY_PEPPER` | API-key hashing secret; changing it invalidates existing integration keys |
| `DISCORD_BOT_INTERNAL_TOKEN` | Separate trusted Discord-client authentication |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | Optional OAuth2 application credentials |
| `DISCORD_REDIRECT_URI` | Exact configured callback `/api/v1/auth/discord/callback` |
| `ATM_SERVICE_TOKEN` | Separate ATM coordinator token, never a reused core/bot secret |
| `ATM_CONFIG_DIR` | Host directory mounted read-only for private ATM configuration |
| `ATM_IDENTITY_MODE` | `online-mode` or explicitly configured trusted `server-auth` |
| `MINECRAFT_VERIFIER_TOKEN` | Optional legacy trusted linking verifier |
| `MAX_REQUEST_BYTES` | HTTP request-body bound |
| `RATE_LIMIT_PER_MINUTE` | Per-process request budget; use an outer proxy for aggregate protection |
| `FORWARDED_ALLOW_IPS` | Explicit trusted proxy addresses for client-address processing |
| `SESSION_TTL_SECONDS` | Inactivity lifetime, up to 30 days |
| `SESSION_ABSOLUTE_TTL_SECONDS` | Maximum session lifetime, up to 90 days |
| `PROJECT_NAME` | Public API display name |
| `VITE_PROJECT_NAME` | Frontend display name, defaults to `Banking System` |
| `VITE_SERVER_URL` | Optional public Minecraft-server website link |
| `VITE_BANK_LOGO_URL` | Optional operator-supplied image URL; no production logo is bundled |

Frontend `VITE_*` values are public build-time configuration, not secret storage. Rebuild `web` after changing them. Keep existing `HORIZON_*` SDK and worker variable names; changing implementation identifiers is unnecessary.

Database passwords must be URL-safe when interpolated into Compose connection URLs. The development helper uses hexadecimal values. Never reuse demonstration or CI credentials for a deployed service.

Privacy maintenance and backup examples under `infra/` require explicit operator review. Set storage paths, retention periods, restore credentials, and the financial retention basis before enabling any timer.

The Minecraft ATM website link uses `PUBLIC_ORIGIN` (HTTP(S) origin only), with a localhost default for local development. Never put credentials into URLs.
