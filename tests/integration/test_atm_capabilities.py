"""ATM chat capabilities use real grants and the verified nickname/UUID pair."""

import uuid

import pytest
from pydantic import SecretStr
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie

api_client = api_fixture


@pytest.mark.asyncio
async def test_chat_capabilities_require_service_identity_and_current_permissions(api_client):
    client, db, settings = api_client
    settings.atm_service_token = SecretStr("test-atm-capabilities-" + "x" * 40)
    user, _ = session_cookie(db)
    player = str(uuid.uuid4())
    nickname = "P" + uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO auth.minecraft_verified_links(user_id,player_uuid,nickname,verified_at) VALUES(%s,%s,%s,now())",
        (user, player, nickname),
    )
    db.commit()
    body = {"nickname": nickname, "player_uuid": player, "instance_id": str(uuid.uuid4())}
    url = "/api/v1/internal/atm/capabilities"
    headers = {"Authorization": "Bearer " + settings.atm_service_token.get_secret_value()}
    assert (await client.post(url, json=body)).status_code == 401
    assert (await client.post(url, headers=headers, json=body)).json() == {"can_test": False, "can_collect": False}
    for wrong in [{"nickname": "WrongPlayer"}, {"player_uuid": str(uuid.uuid4())}]:
        assert (await client.post(url, headers=headers, json={**body, **wrong})).status_code == 403
    for permission in ["admin.atm.read", "admin.atm.manage", "admin.atm.collect"]:
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,%s,%s,'chat preview test')",
            (user, permission, user),
        )
        db.commit()
        response = await client.post(url, headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {"can_test": True, "can_collect": permission == "admin.atm.collect"}
        db.execute("UPDATE auth.user_permissions SET revoked_at=now() WHERE user_id=%s", (user,))
        db.commit()
        assert (await client.post(url, headers=headers, json=body)).json() == {"can_test": False, "can_collect": False}
    db.execute(
        "INSERT INTO auth.user_roles(user_id,role_id,granted_by,reason) "
        "SELECT %s,id,%s,'chat preview role test' FROM auth.admin_roles WHERE system_key='superadmin'",
        (user, user),
    )
    db.commit()
    assert (await client.post(url, headers=headers, json=body)).json() == {"can_test": True, "can_collect": True}
    db.execute("UPDATE auth.user_roles SET revoked_at=now() WHERE user_id=%s", (user,))
    db.commit()
    assert (await client.post(url, headers=headers, json=body)).json() == {"can_test": False, "can_collect": False}
    db.execute("UPDATE auth.application_users SET processing_restricted_at=now() WHERE id=%s", (user,))
    db.commit()
    assert (await client.post(url, headers=headers, json=body)).status_code == 403
