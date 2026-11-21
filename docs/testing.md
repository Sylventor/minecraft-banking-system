# Testing

Tests use synthetic actors and dedicated `horizon_test_*` databases. Never supply production connection strings. The native integration runner creates a fresh database and applies both migration streams; it does not reuse an operator's live financial data.

## Unit checks

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]' -e './sdk[dev]' discord.py==2.6.3
make proto
ENVIRONMENT=test pytest backend/tests sdk/tests bot/tests -q
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm run typecheck
corepack pnpm test
corepack pnpm run lint
corepack pnpm run build
cd ../atm
npm ci --ignore-scripts
npm test
```

The backend test setup selects test mode and synthetic credentials. A dedicated test environment should also override any inherited authentication database URL. Browser/component tests simulate API boundaries; they do not prove a deployed Minecraft or Discord integration works.

## Native financial integration

Build the C++ core as described in [Local development](local-development.md), start a dedicated local PostgreSQL test server, and set:

```bash
export TEST_ADMIN_DATABASE_URL='postgresql://test_admin@127.0.0.1:55432/postgres'
export CORE_EXECUTABLE="$PWD/build/core/horizon-core"
python tests/run_integration.py
```

The administrator needs `CREATEDB` and `CREATEROLE`. Test roles can use explicitly supplied `TEST_HORIZON_FINANCE_PASSWORD` and `TEST_HORIZON_AUTH_PASSWORD`. Keep those values outside source control. The runner stops core processes after tests.

Regression coverage includes authorization/IDOR, scoped keys, sessions, CSRF, money parsing, concurrent replay, overdraft prevention, holds/refunds, payment requests, exchange, organization approvals, ATM evidence, HPay snapshots, and database integrity. The backup/restore tests operate only on generated test databases.

## Public-release checks

```bash
python tools/verify_public_release.py
python tests/security/scan_secrets.py
gitleaks git --redact --log-opts="--all" .
pip-audit
cd frontend && corepack pnpm audit
cd ../atm && npm audit
```

Review scanner findings rather than blindly suppressing them. Known synthetic CI fixtures can resemble secrets; a fixture is never safe to reuse in production. Scan installed dependencies and the actual images you deploy, not only top-level package names.

`docs/verification.md` records checks performed while preparing this copy and their limits. It is not a claim that GitHub Actions, production traffic, container-image scanning, or live provider integrations were tested when those environments were unavailable.

`.gitleaks.toml` retains the default rules and narrowly allows three deterministic fixtures by exact value, rule ID, and path. It does not exclude entire test directories or historical commits. Never reuse these fixtures as service credentials.
