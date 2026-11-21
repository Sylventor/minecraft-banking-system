"""Serve a loopback-only synthetic demo backed by a fresh test database and real core.

Never expose this authenticated local preview through a public reverse proxy.
The tool creates a new horizon_test_* database and never imports production data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/integration"))
sys.path.insert(0, str(ROOT / "backend"))


def main() -> None:
    import psycopg
    import uvicorn
    from psycopg.conninfo import conninfo_to_dict
    from bootstrap import bootstrap
    from test_core_finance import (
        TOKEN,
        account,
        context,
        core,
        fund,
        invoke,
        pb,
        request,
    )

    base = conninfo_to_dict(os.environ["TEST_ADMIN_DATABASE_URL"])
    if (
        base.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or base["dbname"] != "postgres"
    ):
        raise SystemExit(
            "Use a dedicated loopback PostgreSQL test server and its postgres maintenance database."
        )
    config = bootstrap()
    assert conninfo_to_dict(config["TEST_DATABASE_URL"])["dbname"].startswith(
        "horizon_test_"
    )
    os.environ.update(config)
    os.environ["ENVIRONMENT"] = "test"
    from test_api_security import session_cookie
    from app.config import Settings, get_settings
    from app.database import get_session
    from app.main import create_app
    from fastapi.staticfiles import StaticFiles
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    sample = json.loads((ROOT / "demo/sample-data.json").read_text(encoding="utf-8"))
    running = core.__wrapped__()
    stub = next(running)
    try:
        with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
            owner, token = session_cookie(db)
            recipient, _ = session_cookie(db)
            for user, person in [
                (owner, sample["owner"]),
                (recipient, sample["recipient"]),
            ]:
                db.execute(
                    "UPDATE auth.application_users SET display_name=%s,bank_username=%s,language='en',banking_ready=true WHERE id=%s",
                    (person["display_name"], person["username"], user),
                )
            db.commit()
            source = invoke(
                stub.EnsurePrimaryAccount,
                pb.PrimaryAccountRequest(context=context(owner)),
            ).id
            destination = invoke(
                stub.EnsurePrimaryAccount,
                pb.PrimaryAccountRequest(context=context(recipient)),
            ).id
            savings = account(stub, owner)
            for ident, name in [
                (source, "Main account"),
                (destination, "Shop account"),
                (savings, "Savings"),
            ]:
                invoke(
                    stub.UpdateAccount,
                    pb.UpdateAccountRequest(
                        context=context(owner if ident != destination else recipient),
                        account_id=ident,
                        name=name,
                    ),
                )
            fund(stub, db, owner, source, sample["initial_issued_amount"])
            invoke(
                stub.Transfer,
                request(owner, source, destination, sample["sample_transfer_amount"]),
            )
            invoke(stub.Transfer, request(owner, source, savings, "300.00"))
        settings = Settings(
            _env_file=None,
            environment="test",
            auth_database_url=config["TEST_AUTH_DATABASE_URL"].replace(
                "postgresql://", "postgresql+asyncpg://"
            ),
            grpc_target=stub.test_target,
            internal_grpc_token=TOKEN,
            api_key_pepper="test-demo-pepper-" + "x" * 40,
            discord_bot_internal_token="test-demo-bot-" + "x" * 40,
            public_origin="http://127.0.0.1:5190",
            cors_origins="http://127.0.0.1:5190",
        )
        app = create_app(settings)
        get_settings.cache_clear()
        app.dependency_overrides[get_settings] = lambda: settings
        engine = create_async_engine(settings.auth_database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def session():
            async with maker() as value:
                yield value

        app.dependency_overrides[get_session] = session
        app.mount(
            "/",
            StaticFiles(directory=ROOT / "frontend/dist", html=True),
            name="synthetic-demo",
        )

        class LocalTestSession:
            async def __call__(self, scope, receive, send):
                if scope["type"] in {"http", "websocket"}:
                    scope["headers"] = [
                        (key, value)
                        for key, value in scope["headers"]
                        if key != b"cookie"
                    ] + [(b"cookie", ("horizon_session=" + token).encode())]
                await app(scope, receive, send)

        print(
            "Synthetic local demo: http://127.0.0.1:5190 (do not expose publicly)",
            flush=True,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                LocalTestSession(), host="127.0.0.1", port=5190, access_log=False
            )
        )
        duration = int(os.environ.get("DEMO_DURATION_SECONDS", "0"))
        if duration:
            import threading

            timer = threading.Timer(
                max(10, min(duration, 3600)),
                lambda: setattr(server, "should_exit", True),
            )
            timer.daemon = True
            timer.start()
        server.run()
    finally:
        running.close()


if __name__ == "__main__":
    main()
