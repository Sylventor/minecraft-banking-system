from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie

api_client = api_fixture

ORIGIN = {"Origin": "http://localhost:8080"}


async def challenge_for(client, db):
    user, token = session_cookie(db)
    client.cookies.set("horizon_session", token)
    response = await client.post("/api/v1/minecraft/link-code", headers=ORIGIN)
    assert response.status_code == 201, response.text
    return user, response.json()


def attestation(challenge, player=None, nickname=None):
    return {
        "challenge_id": challenge["challenge_id"],
        "code": challenge["code"],
        "player_uuid": player or str(uuid.uuid4()),
        "nickname": nickname or "N" + uuid.uuid4().hex[:12],
        "authentication": "online-mode",
    }


def verifier(settings):
    return {"Authorization": "Bearer " + settings.minecraft_verifier_token.get_secret_value()}


@pytest.mark.asyncio
async def test_only_attested_identities_are_visible_and_code_is_one_time(api_client):
    client, db, settings = api_client
    user, challenge = await challenge_for(client, db)
    assert (await client.get("/api/v1/minecraft")).json()["link"] is None
    stored = db.execute(
        "SELECT code_hash FROM auth.minecraft_link_challenges WHERE id=%s", (challenge["challenge_id"],)
    ).fetchone()[0]
    assert stored == hashlib.sha256(challenge["code"].encode()).hexdigest()
    body = attestation(challenge)
    assert (await client.post("/api/v1/minecraft/verify", json=body, headers=ORIGIN)).status_code == 401
    assert (await client.post("/api/v1/minecraft/verify", json=body, headers=verifier(settings))).status_code == 200
    linked = (await client.get("/api/v1/minecraft")).json()["link"]
    assert linked["player_uuid"] == body["player_uuid"]
    assert linked["nickname"] == body["nickname"]
    assert (await client.post("/api/v1/minecraft/verify", json=body, headers=verifier(settings))).status_code == 409
    assert (
        db.execute(
            "SELECT count(*) FROM auth.audit_events WHERE actor_id=%s AND action='minecraft.link.verified'", (user,)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_wrong_attempts_persist_expiry_rotation_and_offline_attestation_are_rejected(api_client):
    client, db, settings = api_client
    user, challenge = await challenge_for(client, db)
    body = attestation(challenge)
    wrong = {**body, "code": "invalid-code-" + "x" * 20}
    for _ in range(5):
        assert (
            await client.post("/api/v1/minecraft/verify", json=wrong, headers=verifier(settings))
        ).status_code == 401
    assert (await client.post("/api/v1/minecraft/verify", json=body, headers=verifier(settings))).status_code == 429
    assert (await client.post("/api/v1/minecraft/link-code", headers=ORIGIN)).status_code == 429
    db.execute(
        "UPDATE auth.minecraft_link_challenges SET created_at=now()-interval '2 minutes' WHERE user_id=%s", (user,)
    )
    db.commit()
    replacement = await client.post("/api/v1/minecraft/link-code", headers=ORIGIN)
    assert replacement.status_code == 201
    assert (await client.post("/api/v1/minecraft/verify", json=body, headers=verifier(settings))).status_code == 409
    fresh = attestation(replacement.json())
    assert (
        await client.post(
            "/api/v1/minecraft/verify", json={**fresh, "authentication": "offline-mode"}, headers=verifier(settings)
        )
    ).status_code == 422
    db.execute(
        "UPDATE auth.minecraft_link_challenges SET created_at=now()-interval '20 minutes',expires_at=now()-interval '1 minute' WHERE id=%s",
        (fresh["challenge_id"],),
    )
    db.commit()
    assert (await client.post("/api/v1/minecraft/verify", json=fresh, headers=verifier(settings))).status_code == 410


@pytest.mark.asyncio
async def test_two_concurrent_confirmations_create_only_one_link(api_client):
    client, db, settings = api_client
    _, challenge = await challenge_for(client, db)
    body = attestation(challenge)
    replies = await asyncio.gather(
        *[client.post("/api/v1/minecraft/verify", json=body, headers=verifier(settings)) for _ in range(2)]
    )
    assert sorted(response.status_code for response in replies) == [200, 409]
    assert (
        db.execute(
            "SELECT count(*) FROM auth.minecraft_verified_links WHERE player_uuid=%s", (body["player_uuid"],)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_ownership_is_unique_and_relink_requires_explicit_unlink(api_client):
    client, db, settings = api_client
    first_user, first = await challenge_for(client, db)
    body = attestation(first)
    assert (await client.post("/api/v1/minecraft/verify", json=body, headers=verifier(settings))).status_code == 200
    _, second = await challenge_for(client, db)
    collision = attestation(second, body["player_uuid"], body["nickname"])
    assert (
        await client.post("/api/v1/minecraft/verify", json=collision, headers=verifier(settings))
    ).status_code == 409
    # The first user's fresh interactive session is required to remove their link.
    token = uuid.uuid4().hex + uuid.uuid4().hex
    db.execute(
        "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",
        (hashlib.sha256(token.encode()).hexdigest(), first_user),
    )
    db.execute(
        "UPDATE auth.minecraft_link_challenges SET created_at=now()-interval '2 minutes' WHERE user_id=%s",
        (first_user,),
    )
    db.commit()
    client.cookies.set("horizon_session", token)
    new_challenge = (await client.post("/api/v1/minecraft/link-code", headers=ORIGIN)).json()
    assert (
        await client.post("/api/v1/minecraft/verify", json=attestation(new_challenge), headers=verifier(settings))
    ).status_code == 409
    assert (await client.delete("/api/v1/minecraft", headers=ORIGIN)).status_code == 204
    assert (await client.get("/api/v1/minecraft")).json()["link"] is None
    assert (
        await client.post("/api/v1/minecraft/verify", json=attestation(new_challenge), headers=verifier(settings))
    ).status_code == 404


@pytest.mark.asyncio
async def test_disabled_integration_never_claims_ownership(api_client):
    from app.config import get_settings

    client, db, settings = api_client
    _, token = session_cookie(db)
    client.cookies.set("horizon_session", token)
    disabled = settings.model_copy(update={"minecraft_verifier_token": type(settings.minecraft_verifier_token)("")})
    client._transport.app.dependency_overrides[get_settings] = lambda: disabled
    assert (await client.get("/api/v1/minecraft")).json() == {"integration_enabled": False, "link": None}
    assert (await client.post("/api/v1/minecraft/link-code", headers=ORIGIN)).status_code == 503
