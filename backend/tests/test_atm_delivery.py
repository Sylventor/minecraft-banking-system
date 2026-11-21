import uuid

import httpx
import pytest

from app.atm_delivery import inspect_delivery


@pytest.mark.parametrize(
    "payload,status,reason",
    [
        ({"delivered": False, "reason": "receipt_timeout"}, 503, "receipt_timeout"),
        ({"delivered": False, "reason": "secret-code-482913"}, 503, "coordinator_http_503"),
        ({"delivered": False, "reason": {"code": "482913"}}, 503, "coordinator_http_503"),
        ({"delivered": True, "authentication": "online-mode"}, 200, "identity_mode_mismatch"),
        ({"delivered": False}, 200, "delivery_not_confirmed"),
        ({"delivered": True, "authentication": "server-auth", "player_uuid": "bad"}, 200, "invalid_identity"),
        ({"delivered": True, "authentication": "server-auth", "player_uuid": None}, 200, "invalid_identity"),
        ([], 200, "invalid_response"),
    ],
)
def test_delivery_failure_reasons_are_safe_and_specific(payload, status, reason):
    value, reported = inspect_delivery(httpx.Response(status, json=payload), "server-auth")
    assert value is None and reported == reason
    assert "482913" not in reported


def test_valid_receipt_is_not_reclassified_as_a_failure():
    payload = {"delivered": True, "authentication": "server-auth", "player_uuid": str(uuid.uuid4())}
    assert inspect_delivery(httpx.Response(200, json=payload), "server-auth") == (payload, "confirmed")


def test_unstructured_response_never_enters_diagnostic_reason():
    assert inspect_delivery(httpx.Response(502, text="token=private code=482913"), "server-auth") == (
        None,
        "invalid_response",
    )
