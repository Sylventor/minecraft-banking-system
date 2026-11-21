"""Only the persisted language preference; no financial operations or core build."""

import pytest
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie

api_client = api_fixture


@pytest.mark.asyncio
async def test_language_preference_roundtrip_without_changing_profile_content(
    api_client,
):
    client, db, _ = api_client
    user, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    original = db.execute(
        "SELECT display_name,bank_username FROM auth.application_users WHERE id=%s",
        (user,),
    ).fetchone()
    db.commit()
    for language in ("uk-UA", "en", "ru"):
        response = await client.patch(
            "/api/v1/me/settings",
            headers={"Origin": "http://localhost:8080"},
            json={"language": language},
        )
        assert response.status_code == 200, response.text
        assert response.json()["language"] == language
        record = db.execute(
            "SELECT language,display_name,bank_username FROM auth.application_users WHERE id=%s",
            (user,),
        ).fetchone()
        assert record == (language, *original)
        db.commit()
