# Local development

Use an isolated development machine or dedicated test services. Do not point examples at an existing player's database or production Minecraft server.

## Compose application

Requirements: Docker Engine, Compose v2, Python 3.12+, and a free loopback port 8080.

```bash
python tools/create_dev_env.py
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose ps -a
```

The helper creates a new ignored `.env` with independent random development secrets. It fails if that file already exists. The development Compose override permits local HTTP and binds the API to `127.0.0.1:8000`. PostgreSQL and gRPC remain internal.

Open http://localhost:8080. Register a synthetic user with a password. Discord OAuth2 is optional. A new account has a zero balance; registration is not an issuance endpoint.

```bash
docker compose logs --tail=100 backend web core
docker compose stop
```

Stopping preserves the named database volume. `docker compose down -v` would delete local database data and must only be used when deliberately discarding a disposable environment.

## Native core and Python development

On Ubuntu, install a C++20 compiler, CMake 3.24+, gRPC/protobuf development packages and compiler plugins, libpqxx, Google Test, PostgreSQL client utilities, and Python 3.12+.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]' -e './sdk[dev]' discord.py==2.6.3
make proto
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

Windows/MSVC builds use the pinned `vcpkg.json` baseline and `infra/triplets/x64-windows-release.cmake`. A full vcpkg checkout is needed for historical port definitions. Configure `CMAKE_TOOLCHAIN_FILE` and `VCPKG_INSTALLED_DIR` for your local installation; do not copy another developer's cache or absolute paths.

## Synthetic demonstration

The repository contains fictional sample metadata in `demo/sample-data.json`. For an interactive dashboard with ledger-backed balances, `tools/demo.py` uses the existing test bootstrap and a real local core process to create fresh synthetic data.

```bash
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm run build
cd ..
export TEST_ADMIN_DATABASE_URL='postgresql://test_admin@127.0.0.1:55432/postgres'
export CORE_EXECUTABLE="$PWD/build/core/horizon-core"
python tools/demo.py
```

Use a **dedicated local PostgreSQL server**. The test administrator needs `CREATEDB` and `CREATEROLE`. Supply credentials securely when that server requires authentication. On Windows, set `CORE_EXECUTABLE` to `build/core/Release/horizon-core.exe` and use the corresponding PowerShell environment syntax.

The demo binds only to `127.0.0.1:5190`, injects a synthetic local session, and creates a new `horizon_test_*` database. It must never be exposed through a public proxy. It creates no shared demonstration password and imports no production dump. Set `DEMO_DURATION_SECONDS` for an automatic shutdown (10–3600 seconds). The generated test database is retained for inspection; remove only that explicitly identified disposable database when finished.

## Optional clients

See [Minecraft ATM](minecraft-atm.md), [HPay](hpay.md), and `sdk/README.md`. External Discord/Minecraft credentials are never included. Optional clients are disabled until their profiles and private configuration are explicitly supplied.
