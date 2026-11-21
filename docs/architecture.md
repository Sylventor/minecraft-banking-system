# Architecture

## Ownership boundaries

The Financial Core is a C++20 gRPC process. It owns virtual account balances, permissions, holds, issuance, refunds, organizations, payroll, exchange settlement, ATM settlement, and HPay purchase state.

FastAPI owns the `auth` schema: user profiles, password credentials, Discord identities, browser sessions, scoped API keys, administrative role assignments, support, news, and presentation settings. The `horizon_auth` PostgreSQL role has no financial-schema access. The `horizon_finance` role cannot read authentication secrets.

Both schemas normally reside in the same PostgreSQL database. `database/finance/alembic` and `backend/alembic` are independent migration streams. Their current heads are `finance_0012` and `auth_0013`. Applications refuse readiness when required migrations are missing.

## Request flow

1. nginx serves static frontend assets and proxies permitted API routes.
2. FastAPI authenticates a browser cookie, scoped integration key, or dedicated internal service token.
3. The API selects an actor and forwards a bounded, typed request over private gRPC.
4. The core checks the actor's current account/organization permissions and financial authority.
5. A PostgreSQL transaction serializes affected records, writes paired ledger entries and operation metadata, and commits atomically.
6. The API returns the result. Notification relays consume committed events independently.

Money is never stored as a separate authoritative balance in the browser, Discord bot, SDK, or Minecraft client. API caches and user-facing summaries do not replace the ledger.

## Services

`postgres`, `finance-migrations`, `auth-migrations`, `core`, `backend`, and `web` provide the basic application. `payroll-worker` and `exchange-worker` perform scheduled organization/exchange work. `bot` and `atm` are optional Compose profiles. `privacy-worker` is an explicitly operated maintenance task, not an automatic invitation to erase data.

The `internal` Docker network isolates PostgreSQL and gRPC. Services needing outbound provider connections also join `egress`. Default published application ports bind to loopback. A deployment must supply TLS at a trusted outer reverse proxy.

## Integrations and trust

Discord and the Python SDK call the API. The ATM coordinator uses its own service token and a verified nickname/UUID pair. The core fences ATM coordinator leases and reconciles physical item evidence. The optional Fabric client only sends normal Minecraft private-message commands; it has no database or banking-service token.

The private gRPC connection currently uses an internal token and relies on network isolation. It is not a public API or an independently encrypted zero-trust boundary. Spreading services across machines requires a reviewed encrypted transport design.

## Compatibility

Legacy `horizon` namespaces, Python package names, protobuf package names, SQL roles, environment-variable names, browser storage keys, and selected binary names remain intentionally stable. They are implementation identifiers, not an additional public brand. Public display configuration is described in [Configuration](configuration.md).
