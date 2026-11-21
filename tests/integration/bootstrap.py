"""Create a fresh, explicitly named test database and apply both migration streams.

Usage: TEST_ADMIN_DATABASE_URL=postgresql://.../postgres python tests/integration/bootstrap.py
The utility never drops an existing database or modifies production data.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy.engine import URL


def bootstrap(target: str = "head") -> dict[str, str]:
    base = os.environ["TEST_ADMIN_DATABASE_URL"]
    if conninfo_to_dict(base)["dbname"] != "postgres":
        raise RuntimeError(
            "bootstrap must connect to the maintenance database postgres"
        )
    dbname = "horizon_test_" + uuid.uuid4().hex[:12]
    passwords = {}
    for role, parent_variable in (
        ("horizon_finance", "TEST_FINANCE_DATABASE_URL"),
        ("horizon_auth", "TEST_AUTH_DATABASE_URL"),
    ):
        # Nested legacy-upgrade tests use the same restricted roles. Reuse the
        # runner's test credentials instead of returning a new, invalid password
        # when those roles already exist (important with SCRAM in Linux CI).
        inherited = os.environ.get(parent_variable)
        previous = conninfo_to_dict(inherited).get("password") if inherited else None
        passwords[role] = (
            os.environ.get("TEST_" + role.upper() + "_PASSWORD")
            or previous
            or secrets.token_urlsafe(32)
        )
    with psycopg.connect(base, autocommit=True) as db:
        for role in ("horizon_finance", "horizon_auth"):
            if not db.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone():
                db.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(passwords[role])
                    )
                )
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    opts = conninfo_to_dict(base)
    opts["dbname"] = dbname
    admin = make_conninfo(**opts)
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(dbname)
            )
        )
        db.execute(
            sql.SQL(
                "GRANT CONNECT ON DATABASE {} TO horizon_finance,horizon_auth"
            ).format(sql.Identifier(dbname))
        )
        db.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        db.execute("CREATE SCHEMA finance AUTHORIZATION horizon_finance")
        db.execute("CREATE SCHEMA auth AUTHORIZATION horizon_auth")
    root = Path(__file__).resolve().parents[2]
    result = {"TEST_DATABASE_URL": admin}
    for folder, var, role, ini in (
        ("database/finance", "FINANCE_DATABASE_URL", "horizon_finance", "alembic.ini"),
        ("backend", "AUTH_DATABASE_URL", "horizon_auth", "alembic-auth.ini"),
    ):
        # SQLAlchemy URLs must be URLs; psycopg connection strings are kept in test metadata below.
        url = URL.create(
            "postgresql",
            username=role,
            password=passwords[role],
            host=opts["host"],
            port=int(opts.get("port", "5432")),
            database=dbname,
        ).render_as_string(hide_password=False)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                ini,
                "upgrade",
                target
                if target == "head"
                else ("finance_" if role == "horizon_finance" else "auth_") + target,
            ],
            cwd=root / folder,
            env={**os.environ, var: url},
            check=True,
        )
        result["TEST_" + var] = url
    print("Created isolated test database: " + dbname)
    return result


if __name__ == "__main__":
    bootstrap()
