# Deployment

The portfolio copy is independent of any existing deployment. It has no production database, server address, credentials, or automatic publication step. Review it in an isolated environment before operating a service for real players.

## Network boundary

Keep PostgreSQL, gRPC, and internal service endpoints private. Publish the Web Dashboard on loopback and terminate HTTPS at an explicitly trusted outer proxy. The development override is for local HTTP testing only.

Set `ENVIRONMENT=production`, an HTTPS `PUBLIC_ORIGIN`, explicit HTTPS `CORS_ORIGINS`, and independent high-entropy secrets. Configure trusted forwarded addresses narrowly. Discord redirect URIs must match the application origin and callback exactly.

Default nginx configuration blocks `/api/v1/internal/`, escapes request logs, omits query strings from access-log fields, and applies a Content Security Policy. Its Docker resolver refreshes the backend address after container replacement. Keep the WebSocket proxy configuration when customizing support live updates.

## Build and readiness

```bash
docker compose build finance-migrations auth-migrations core backend web
docker compose up -d
docker compose ps -a
docker compose logs --tail=100 backend core web
```

The migration containers apply separate schema streams before dependent services start. The backend `/readyz` checks both the auth revision and core readiness. A completed migration container with exit code zero is expected.

For an upgrade, first verify a recoverable backup on an isolated restore target. Read the migration diff, stop affected writers when required, run only the necessary migrations, and validate readiness and reconciliation. Do not use a public demo script or direct balance edits to repair production.

If a custom nginx configuration still caches the old backend address, reload or restart the `web` service after replacing `backend`.

## Optional integrations

Enable `bot` and `atm` only after creating private credentials and configuration. Use dedicated test provider accounts first. The HPay mod is a client-side convenience, not a financial authority.

Host hardening, TLS renewal, off-host backups, monitoring, provider-account recovery, abuse response, and operational privacy decisions remain deployment responsibilities. The repository's local tests do not verify a particular hosting environment.
