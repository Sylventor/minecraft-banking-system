"""Real PostgreSQL support: isolation, permissions, retries and notifications."""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie

api_client = api_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


@pytest_asyncio.fixture
async def support_client(api_client):
    client, db, settings = api_client
    user, cookie = session_cookie(db)
    operator, operator_cookie = session_cookie(db)
    stranger, stranger_cookie = session_cookie(db)
    for permission in ("read", "reply", "manage"):
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,%s,%s,'Support test')",
            (operator, "admin.support." + permission, operator),
        )
    db.commit()
    client.cookies.set("horizon_session", cookie)
    return (
        client,
        db,
        settings,
        user,
        cookie,
        operator,
        operator_cookie,
        stranger,
        stranger_cookie,
    )


async def create(s):
    body = {
        "title": "Проблема с банкоматом",
        "category": "atm",
        "body": "Не завершилась операция",
        "client_id": str(uuid.uuid4()),
    }
    response = await s[0].post("/api/v1/support/tickets", headers=ORIGIN, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


@pytest.mark.asyncio
async def test_support_private_history_notes_assignment_notifications_and_replay(
    support_client,
):
    s = support_client
    client, db, _, user, cookie, operator, operator_cookie, _, stranger_cookie = s
    t, body = await create(s)
    url = "/api/v1/support/tickets/" + t["id"]
    admin = "/api/v1/admin/support/tickets/" + t["id"]
    replay = await client.post("/api/v1/support/tickets", headers=ORIGIN, json=body)
    assert replay.status_code == 201 and replay.json()["id"] == t["id"]
    client.cookies.set("horizon_session", stranger_cookie)
    assert (await client.get(url)).status_code == 404
    assert (
        await client.get(url + "/messages", params={"before": 1000})
    ).status_code == 404
    assert (await client.get("/api/v1/admin/support/tickets")).status_code == 403
    client.cookies.set("horizon_session", operator_cookie)
    assigned = await client.patch(admin, headers=ORIGIN, json={"assigned_to": operator})
    assert assigned.status_code == 200, assigned.text
    note = await client.post(
        admin + "/notes",
        headers=ORIGIN,
        json={"body": "Секретная заметка оператора", "client_id": str(uuid.uuid4())},
    )
    assert note.status_code == 200, note.text
    notes_history = await client.get(admin + "/notes", params={"before": 1000000000})
    assert notes_history.status_code == 200 and len(notes_history.json()["items"]) == 1
    reply_body = {
        "body": "<script>alert('xss')</script> Проверяем проблему",
        "client_id": str(uuid.uuid4()),
    }
    response = await client.post(admin + "/messages", headers=ORIGIN, json=reply_body)
    assert response.status_code == 200 and response.json()["status"] == "waiting", (
        response.text
    )
    assert (
        await client.post(admin + "/messages", headers=ORIGIN, json=reply_body)
    ).status_code == 200
    client.cookies.set("horizon_session", cookie)
    value = (await client.get(url)).json()
    assert "notes" not in value and "Секретная" not in str(value)
    assert (
        await client.get(url + "/notes", params={"before": 1000000000})
    ).status_code == 404
    assert sum(m["sender_kind"] == "staff" for m in value["messages"]) == 1
    assert any("<script>" in m["body"] for m in value["messages"])
    assert (await client.get("/api/v1/support/unread")).json()["unread"] == 1
    marked = await client.post(
        url + "/read", headers=ORIGIN, json={"through_id": value["messages"][-1]["id"]}
    )
    assert marked.status_code == 200
    assert (await client.get("/api/v1/support/unread")).json() == {"unread": 0}
    assert (
        db.execute(
            "SELECT count(*) FROM auth.user_notifications WHERE user_id=%s AND kind='support'",
            (user,),
        ).fetchone()[0]
        == 1
    )
    db.commit()
    assert (
        await client.patch(url, headers=ORIGIN, json={"status": "closed"})
    ).status_code == 200
    assert (
        await client.post(
            url + "/messages",
            headers=ORIGIN,
            json={"body": "Снова", "client_id": str(uuid.uuid4())},
        )
    ).status_code == 409
    assert (
        await client.patch(url, headers=ORIGIN, json={"status": "open"})
    ).status_code == 200


@pytest.mark.asyncio
async def test_support_author_cannot_be_spoofed_or_manage_staff_state(support_client):
    s = support_client
    client = s[0]
    t, _ = await create(s)
    url = "/api/v1/support/tickets/" + t["id"]
    assert (
        await client.patch(url, headers=ORIGIN, json={"status": "working"})
    ).status_code == 403
    assert (
        await client.patch(url, headers=ORIGIN, json={"assigned_to": s[5]})
    ).status_code == 403
    assert (
        await client.post(
            url + "/messages",
            headers=ORIGIN,
            json={"body": "fake", "client_id": str(uuid.uuid4()), "author_id": s[5]},
        )
    ).status_code == 422
    assert (
        await client.post(
            url + "/messages",
            headers=ORIGIN,
            json={"body": "x" * 4001, "client_id": str(uuid.uuid4())},
        )
    ).status_code == 422
    assert (
        await client.post(
            url + "/messages",
            headers={"Origin": "https://evil.example"},
            json={"body": "fake", "client_id": str(uuid.uuid4())},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_support_search_pagination_and_rate_limit(support_client):
    s = support_client
    client = s[0]
    t, _ = await create(s)
    for _ in range(4):
        await create(s)
    response = await client.post(
        "/api/v1/support/tickets",
        headers=ORIGIN,
        json={
            "title": "Too many",
            "category": "other",
            "body": "Wait",
            "client_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 429
    client.cookies.set("horizon_session", s[6])
    found = await client.get(
        "/api/v1/admin/support/tickets",
        params={"category": "atm", "status": "open", "q": str(t["number"])},
    )
    assert found.status_code == 200 and any(
        v["id"] == t["id"] for v in found.json()["items"]
    ), found.text


@pytest.mark.asyncio
async def test_support_websocket_authentication_origin_and_revocation(support_client):
    s = support_client
    t, _ = await create(s)
    app = s[0]._transport.app
    with TestClient(app) as sync:
        with pytest.raises(WebSocketDisconnect):
            with sync.websocket_connect("/api/v1/support/live", headers=ORIGIN):
                pass
        sync.cookies.set("horizon_session", s[4])
        with pytest.raises(WebSocketDisconnect):
            with sync.websocket_connect(
                "/api/v1/support/live", headers={"Origin": "https://evil.example"}
            ):
                pass
        with pytest.raises(WebSocketDisconnect):
            with sync.websocket_connect("/api/v1/support/live?admin=1", headers=ORIGIN):
                pass
        with sync.websocket_connect(
            "/api/v1/support/live", headers=ORIGIN
        ) as websocket:
            assert websocket.receive_json()["event"] == "support.changed"
            sync.cookies.set("horizon_session", s[6])
            reply = sync.post(
                "/api/v1/admin/support/tickets/" + t["id"] + "/messages",
                headers=ORIGIN,
                json={
                    "body": "Ответ в реальном времени",
                    "client_id": str(uuid.uuid4()),
                },
            )
            assert reply.status_code == 200, reply.text
            assert websocket.receive_json()["unread"] > 0
            s[1].execute(
                "UPDATE auth.user_sessions SET revoked_at=now() WHERE user_id=%s",
                (s[3],),
            )
            s[1].commit()
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()
        sync.cookies.set("horizon_session", s[6])
        with sync.websocket_connect(
            "/api/v1/support/live?admin=1", headers=ORIGIN
        ) as websocket:
            assert websocket.receive_json()["event"] == "support.changed"
            s[1].execute(
                "UPDATE auth.user_permissions SET revoked_at=now() WHERE user_id=%s AND permission LIKE 'admin.support.%%'",
                (s[5],),
            )
            s[1].commit()
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()
