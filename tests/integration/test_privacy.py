"""Disposable PostgreSQL + real C++ checks for irreversible privacy operations."""

import json
import uuid
from urllib.parse import parse_qs, urlparse

import grpc
import pytest
from test_core_finance import account, balance, core, fund, invoke, pb, reconciled
from test_api_security import api_client, session_cookie
from test_banking_experience import connect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import text
from app.privacy import cleanup_auth, erase_auth


def privacy(core, action, user, **payload):
    return json.loads(
        invoke(
            core.Privacy,
            pb.BusinessRequest(
                context=pb.RequestContext(
                    actor_id="00000000-0000-0000-0000-000000000028",
                    source="privacy",
                    request_id=str(uuid.uuid4()),
                    correlation_id=str(uuid.uuid4()),
                ),
                action=action,
                payload=json.dumps({"user_id": user, **payload}),
            ),
        ).json
    )


def test_core_refuses_funded_erasure_and_preserves_ledger(core, database):
    user = str(uuid.uuid4())
    target = account(core, user)
    fund(core, database, user, target, "12.34")
    blocked = privacy(core, "erase", user, basis="verified test basis")
    assert blocked["blocked"]["account_funds"] == 1
    assert balance(core, user, target).balance.amount == "12.34"
    reconciled(database)


def test_core_erasure_is_idempotent_and_rejects_forged_actor(core, database):
    user = str(uuid.uuid4())
    target = account(core, user)
    with pytest.raises(grpc.RpcError):
        invoke(
            core.Privacy,
            pb.BusinessRequest(
                context=pb.RequestContext(actor_id=user, source="privacy"),
                action="erase",
                payload=json.dumps({"user_id": user, "basis": "test"}),
            ),
        )
    privacy(core, "erase", user, basis="verified test basis")
    privacy(core, "erase", user, basis="verified test basis")
    assert database.execute(
        "SELECT status,name,owner_principal_id FROM finance.accounts WHERE id=%s",
        (target,),
    ).fetchone() == ("closed", "Deleted", None)
    with pytest.raises(grpc.RpcError):
        account(core, user)
    reconciled(database)


@pytest.mark.asyncio
async def test_staff_verification_export_and_user_isolation(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    staff, token = session_cookie(db)
    subject, _ = session_cookie(db)
    client.cookies.set("horizon_session", token)
    assert (await client.get("/api/v1/admin/privacy/requests")).status_code == 403
    for permission in ["read", "manage", "export", "erase"]:
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,%s,%s,'privacy integration')",
            (staff, "admin.privacy." + permission, staff),
        )
    db.commit()
    origin = {"Origin": "http://localhost:8080"}
    response = await client.post(
        "/api/v1/admin/privacy/requests",
        headers=origin,
        json={"user_id": subject, "kind": "access", "source": "email"},
    )
    assert response.status_code == 201, response.text
    path = "/api/v1/admin/privacy/requests/" + response.json()["id"]
    export = {"schema_name": "auth", "table": "user_sessions", "offset": 0}
    assert (
        await client.post(path + "/export", headers=origin, json=export)
    ).status_code == 403
    assert (
        await client.post(
            path + "/actions",
            headers=origin,
            json={
                "action": "verify",
                "confirmed": True,
                "reason": "Ownership verified through existing session",
            },
        )
    ).status_code == 200
    result = await client.post(path + "/export", headers=origin, json=export)
    assert result.status_code == 200, result.text
    assert "token_hash" not in result.text and "staff_review" in result.text
    assert all(row["user_id"] == subject for row in result.json()["rows"])
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_new_discord_profile_requires_state_bound_age_confirmation(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    before = db.execute("SELECT count(*) FROM auth.application_users").fetchone()[0]
    db.commit()
    start = await client.get("/api/v1/auth/discord/login")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    refused = await client.get(
        "/api/v1/auth/discord/callback", params={"state": state, "code": "test"}
    )
    assert (
        refused.status_code == 303
        and "/register?age=required" in refused.headers["location"]
    )
    assert (
        db.execute("SELECT count(*) FROM auth.application_users").fetchone()[0]
        == before
    )
    db.commit()
    start = await client.get("/api/v1/auth/discord/login?age_confirmed=true")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    accepted = await client.get(
        "/api/v1/auth/discord/callback", params={"state": state, "code": "test"}
    )
    assert accepted.status_code == 302, accepted.text
    user = (await client.get("/api/v1/me")).json()["id"]
    assert db.execute(
        "SELECT age_confirmed_at IS NOT NULL FROM auth.application_users WHERE id=%s",
        (user,),
    ).fetchone()[0]


def test_financial_minimization_keeps_amounts_and_holds(core, database):
    user = str(uuid.uuid4())
    target = account(core, user)
    operation = fund(core, database, user, target, "7.00")
    database.execute(
        "INSERT INTO finance.privacy_subjects(user_id,status,retention_basis) VALUES(%s,'erasing','test')",
        (user,),
    )
    database.execute(
        "INSERT INTO finance.privacy_holds(user_id,expires_at,basis) VALUES(%s,now()+interval '1 day','active dispute')",
        (user,),
    )
    database.commit()
    assert privacy(core, "erase", user, basis="test")["blocked"]["legal_hold"]
    assert (
        database.execute(
            "SELECT amount,reason FROM finance.financial_operations WHERE id=%s",
            (operation.id,),
        ).fetchone()[1]
        != "[redacted]"
    )
    reconciled(database)


def test_old_terminal_payment_is_minimized_without_changing_amount(core, database):
    user, payer = str(uuid.uuid4()), str(uuid.uuid4())
    target = account(core, user)
    ident = uuid.uuid4()
    database.execute(
        "INSERT INTO finance.payment_requests(id,creator_id,payer_id,recipient_account_id,amount,description,status,external_id,metadata,created_at,expires_at,creation_key,fingerprint) "
        "VALUES(%s,%s,%s,%s,5.25,'private description','expired','private reference','{\"private\":\"value\"}',now()-interval '400 days',now()-interval '399 days',%s,'test')",
        (ident, user, payer, target, str(uuid.uuid4())),
    )
    database.commit()
    result = privacy(core, "minimize", user, basis="reviewed test grounds")
    assert result["ledger_rows_deleted"] == 0
    assert database.execute(
        "SELECT amount::text,description,external_id,metadata,status FROM finance.payment_requests WHERE id=%s",
        (ident,),
    ).fetchone() == ("5.25", "[redacted]", None, {}, "expired")
    database.commit()
    assert (
        privacy(core, "minimize", user, basis="reviewed test grounds")[
            "ledger_rows_deleted"
        ]
        == 0
    )
    reconciled(database)


@pytest.mark.asyncio
async def test_auth_erasure_removes_identity_copies_and_sessions(api_client):
    _, db, settings = api_client
    subject, _ = session_cookie(db)
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(gen_random_uuid(),%s,'discord',%s,'private name')",
        (subject, str(uuid.uuid4().int)),
    )
    db.execute(
        "INSERT INTO auth.minecraft_verified_links(user_id,player_uuid,nickname,authentication,verified_at) VALUES(%s,%s,'PlayerName','online-mode',now())",
        (subject, uuid.uuid4()),
    )
    db.execute(
        "INSERT INTO auth.bank_username_history(id,user_id,old_name,new_name) VALUES(gen_random_uuid(),%s,NULL,'PrivateName')",
        (subject,),
    )
    db.commit()
    engine = create_async_engine(settings.auth_database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            await erase_auth(session, uuid.UUID(subject))
            await session.commit()
            await erase_auth(session, uuid.UUID(subject))
            await session.commit()
        for table, column in [
            ("external_identities", "user_id"),
            ("minecraft_verified_links", "user_id"),
            ("user_sessions", "user_id"),
            ("bank_username_history", "user_id"),
        ]:
            assert (
                db.execute(
                    f"SELECT count(*) FROM auth.{table} WHERE {column}=%s", (subject,)
                ).fetchone()[0]
                == 0
            )
        assert db.execute(
            "SELECT status,display_name,bank_username,erased_at IS NOT NULL FROM auth.application_users WHERE id=%s",
            (subject,),
        ).fetchone() == ("archived", "", None, True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retention_uses_notification_periods_and_preserves_hold(api_client):
    _, db, settings = api_client
    subject, _ = session_cookie(db)
    ids = [uuid.uuid4() for _ in range(4)]
    for ident, age, read_age in zip(
        ids, [40, 100, 40, 100], [31, None, None, None], strict=True
    ):
        db.execute(
            "INSERT INTO auth.user_notifications(id,user_id,event_key,kind,title,body,created_at,read_at) VALUES(%s,%s,%s,'security','private','private',now()-(%s||' days')::interval,CASE WHEN %s::int IS NULL THEN NULL ELSE now()-(%s||' days')::interval END)",
            (ident, subject, str(ident), age, read_age, read_age),
        )
    db.execute(
        "INSERT INTO auth.privacy_holds(user_id,basis,expires_at,created_by) VALUES(%s,'verified dispute',now()+interval '1 day',%s)",
        (subject, subject),
    )
    db.commit()
    engine = create_async_engine(settings.auth_database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            await cleanup_auth(session)
            await session.commit()
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM auth.user_notifications WHERE user_id=:u"
                    ),
                    {"u": uuid.UUID(subject)},
                )
                == 4
            )
            await session.execute(
                text(
                    "UPDATE auth.privacy_holds SET released_at=now() WHERE user_id=:u"
                ),
                {"u": uuid.UUID(subject)},
            )
            await cleanup_auth(session)
            await session.commit()
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM auth.user_notifications WHERE user_id=:u"
                    ),
                    {"u": uuid.UUID(subject)},
                )
                == 1
            )
    finally:
        await engine.dispose()
