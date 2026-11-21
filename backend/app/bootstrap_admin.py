"""Server-only first Superadmin procedure. No HTTP endpoint, no financial grants."""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import get_settings
from .experience import audit, notify, rows


async def bootstrap(identifier: str) -> str:
    engine = create_async_engine(get_settings().auth_database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(220022)"))
            existing = await session.scalar(
                text("""SELECT EXISTS(SELECT 1 FROM auth.user_roles ur
              JOIN auth.admin_roles r ON r.id=ur.role_id JOIN auth.application_users u ON u.id=ur.user_id
              WHERE r.system_key='superadmin' AND ur.revoked_at IS NULL AND u.status='active')""")
            )
            if existing:
                raise RuntimeError(
                    "An active Superadmin already exists; assign additional roles through the administration panel"
                )
            users = await rows(
                session,
                """SELECT DISTINCT u.id FROM auth.application_users u
              LEFT JOIN auth.external_identities e ON e.user_id=u.id AND e.provider='discord'
              WHERE u.status='active' AND (u.id::text=:key OR lower(u.bank_username)=lower(:key) OR e.external_id=:key)""",
                {"key": identifier.removeprefix("@")},
            )
            if len(users) != 1:
                raise RuntimeError(
                    "Exactly one active registered user must match the bank username, profile UUID or Discord ID"
                )
            user = users[0]["id"]
            await session.execute(
                text("""INSERT INTO auth.user_roles(user_id,role_id,granted_by,reason)
              SELECT :user,id,:user,'First Superadmin: server operator bootstrap' FROM auth.admin_roles WHERE system_key='superadmin'
              ON CONFLICT(user_id,role_id) DO UPDATE SET revoked_at=NULL,granted_by=EXCLUDED.granted_by,reason=EXCLUDED.reason,created_at=now()"""),
                {"user": user},
            )
            audit(session, str(user), "role.bootstrap", str(user), {"procedure": "server-only"})
            await notify(
                session,
                str(user),
                "bootstrap:" + str(uuid.uuid4()),
                "security",
                "Назначен Superadmin",
                "Оператор сервера назначил вам первую административную роль Superadmin.",
            )
            await session.commit()
            return str(user)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="Bank username, profile UUID or Discord ID")
    args = parser.parse_args()
    try:
        print("Superadmin profile UUID: " + asyncio.run(bootstrap(args.user)))
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
