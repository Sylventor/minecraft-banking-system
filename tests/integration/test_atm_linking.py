import uuid
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie

api_client = api_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


def provider(monkeypatch, settings, *, delivered=True):
    from app.routes import minecraft_atm

    settings.atm_service_token = SecretStr("test-atm-separate-" + "a" * 40)
    sent = {}

    class Delivery:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            sent.update(kwargs["json"])
            return httpx.Response(
                200 if delivered else 503,
                request=httpx.Request("POST", url),
                json={
                    "delivered": delivered,
                    "player_uuid": str(uuid.uuid4()),
                    "authentication": "online-mode",
                },
            )

    monkeypatch.setattr(
        minecraft_atm,
        "httpx",
        SimpleNamespace(AsyncClient=Delivery, HTTPError=httpx.HTTPError),
    )
    return sent


@pytest.mark.asyncio
async def test_website_code_is_delivered_hashed_bound_and_one_time(
    api_client, monkeypatch
):
    client, db, settings = api_client
    user, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    sent = provider(monkeypatch, settings)
    nickname = "P" + uuid.uuid4().hex[:12]
    started = await client.post(
        "/api/v1/minecraft/link/start", headers=ORIGIN, json={"nickname": nickname}
    )
    assert started.status_code == 201, started.text
    assert "code" not in started.json()
    challenge = started.json()["challenge_id"]
    stored = db.execute(
        "SELECT code_hash,extract(epoch from expires_at-created_at),status FROM auth.minecraft_atm_challenges WHERE id=%s",
        (challenge,),
    ).fetchone()
    assert stored[0] != sent["code"] and len(stored[0]) == 64
    assert 295 <= stored[1] <= 305 and stored[2] == "delivered"
    assert (
        await client.post(
            "/api/v1/minecraft/link/start", headers=ORIGIN, json={"nickname": nickname}
        )
    ).status_code == 429
    body = {"challenge_id": challenge, "code": sent["code"]}
    verified = await client.post(
        "/api/v1/minecraft/link/verify", headers=ORIGIN, json=body
    )
    assert verified.status_code == 200, verified.text
    assert (
        await client.post("/api/v1/minecraft/link/verify", headers=ORIGIN, json=body)
    ).status_code == 409
    status = (await client.get("/api/v1/minecraft/link/status")).json()
    assert status["link"]["nickname"] == nickname and status["pending"] is None
    assert (
        db.execute(
            "SELECT count(*) FROM auth.audit_events WHERE actor_id=%s AND action='minecraft.atm.link.verified'",
            (user,),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_delivery_failure_never_claims_success_and_attempts_survive_rejection(
    api_client, monkeypatch
):
    client, db, settings = api_client
    user, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    provider(monkeypatch, settings, delivered=False)
    response = await client.post(
        "/api/v1/minecraft/link/start",
        headers=ORIGIN,
        json={"nickname": "P" + uuid.uuid4().hex[:12]},
    )
    assert response.status_code == 503 and "Не удалось отправить" in response.text
    assert (
        db.execute(
            "SELECT status FROM auth.minecraft_atm_challenges WHERE user_id=%s", (user,)
        ).fetchone()[0]
        == "failed"
    )
    user, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    sent = provider(monkeypatch, settings)
    started = await client.post(
        "/api/v1/minecraft/link/start",
        headers=ORIGIN,
        json={"nickname": "P" + uuid.uuid4().hex[:12]},
    )
    assert started.status_code == 201, started.text
    challenge = started.json()["challenge_id"]
    bad_code = "000000" if sent["code"] != "000000" else "111111"
    for _ in range(5):
        assert (
            await client.post(
                "/api/v1/minecraft/link/verify",
                headers=ORIGIN,
                json={"challenge_id": challenge, "code": bad_code},
            )
        ).status_code == 401
    assert db.execute(
        "SELECT attempts,status FROM auth.minecraft_atm_challenges WHERE id=%s",
        (challenge,),
    ).fetchone() == (5, "locked")
    assert (
        await client.post(
            "/api/v1/minecraft/link/verify",
            headers=ORIGIN,
            json={"challenge_id": challenge, "code": sent["code"]},
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_service_and_administrative_routes_reject_regular_user(api_client):
    client, db, _ = api_client
    _, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    assert (
        await client.post(
            "/api/v1/internal/atm/lease",
            headers=ORIGIN,
            json={
                "instance_id": str(uuid.uuid4()),
                "max_amount": 64,
                "player_distance": 15,
            },
        )
    ).status_code == 401
    assert (await client.get("/api/v1/admin/atm")).status_code == 403
    assert (
        await client.patch(
            "/api/v1/admin/atm/devices/atm-1", headers=ORIGIN, json={"enabled": True}
        )
    ).status_code == 403
