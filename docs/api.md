# API

The versioned REST prefix is `/api/v1`. The English HTML guide is served at `/api/docs`; its filtered schema is `/api/openapi.json`, with a checked-in snapshot at [openapi.json](openapi.json). Regenerate it with `PYTHONPATH=backend python infra/scripts/export-openapi.py` after generating the protobuf bindings. Internal and administrative schemas are omitted from the public guide. Source files still contain the complete implementation for review.

## Authentication

Browser sessions use a random HttpOnly cookie backed by a hash in PostgreSQL. Production cookies are Secure and SameSite=Lax. Cookie-authenticated writes require the configured same-origin `Origin` header. Passwords use Argon2id. Optional Discord OAuth2 uses an expiring, single-use state tied to the browser.

An integration sends `Authorization: Bearer <key>`. Keys are shown once, stored as hashes, constrained by scopes and an account allowlist, and checked against the owner's current state and core permissions. An API key does not grant administrative privileges or a browser session.

```bash
curl "$HORIZON_API_URL/api/v1/accounts" \
  -H "Authorization: Bearer $HORIZON_API_KEY"
```

Keep the variables outside source control. Never put a key in a URL or browser bundle.

## Financial request rules

Amounts are canonical decimal strings such as `"125.50"`, with exactly two fractional digits. The supported maximum is `9999999999999999.99 DB`. Mutating amounts must be positive; extra decimals are rejected. Current fees are zero.

Use a stable `Idempotency-Key` for a financial command. Reuse that key only with an identical request after an uncertain result. The core binds the key to the actor, operation type, and payload. Do not generate a fresh key merely because a response was lost.

## Main endpoint groups

| Area | Representative paths |
| --- | --- |
| Authentication | `/auth/register`, `/auth/login`, `/auth/methods`, `/auth/discord/login` |
| Sessions | `/me/sessions`, `/auth/session/refresh`, `/me/sessions/revoke-others` |
| Profile | `/me`, `/me/settings`, `/dashboard` |
| Accounts | `/accounts`, `/accounts/primary`, `/accounts/{id}/balance` |
| Transfers | `/transfer-quotes`, `/transfer-quotes/{id}`, `/transfers`, `/receipts/{id}` |
| Integrations | `/api-keys`, `/payment-requests` |
| Organizations | `/organizations`, `/organizations/{id}/accounts`, `/organizations/{id}/members` |
| Business | `/organizations/{id}/analytics/{kind}`, payroll routes under that organization |
| Exchange | `/exchange/stocks`, `/exchange/orders`, issuer and dividend routes |
| Minecraft | `/minecraft/link/start`, `/minecraft/link/verify`, `/minecraft/link/status` |
| HPay | `/organizations/{id}/hpay/products`, `/organizations/{id}/hpay/sales` |
| Content | `/news`, `/terms`, `/support/tickets`, `/notifications` |

Paths in the table are relative to `/api/v1`; consult the generated schema for exact methods, request models, and pagination parameters.

## Transfer confirmations

Resolve and explicitly select a recipient before creating a transfer quote. The quote snapshots the destination, source, amount, description, and expiry. Confirm the displayed quote with its stable ID/key. Internal transfers additionally require both accounts to belong to the user or be accessible through organization membership. An integration must allowlist both accounts where that workflow requires it.

## Merchant payment requests

A merchant key belongs to one organization and has payment-request-specific scopes and receiving-account IDs. Creating a request proposes a payment; it does not debit the payer. The payer confirms through an interactive session. Deliver a game product only after reading `status=paid` with a committed transaction ID. Decline/cancel/expiry are separate states, and refunds remain new financial operations.

## Organizations, payroll, and exchange

Organization membership is distinct from administrative permissions. A payroll batch, automatic mandate, or dividend requires the corresponding organization permission. Owners may approve their own batches/mandates; other users require the existing independent-approver workflow. Automatic payroll runs in a separate worker.

Public stock-market data is readable without account access. Portfolio/history endpoints and trading require the corresponding scopes and current account permissions. Limit orders reserve money or shares; market orders use explicit price/budget bounds and cancel unfilled IOC remainders. These are virtual shares only.

## Errors

Errors use `{error, code, request_id}`. Typical statuses include 401 for missing/invalid authentication, 403 for denied access, 409 for state conflicts, 422 for invalid input/limits, 429 for request budgets, and 503/504 for temporary dependencies. The UI may localize error text; clients should rely on status and stable codes rather than exact prose.
