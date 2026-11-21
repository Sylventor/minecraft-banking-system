import httpx
import pytest

from horizon_sdk.client import HorizonClient, HorizonError


def test_financial_write_sends_caller_idempotency_key() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["Idempotency-Key"]
        return httpx.Response(
            201,
            json={
                "id": "x",
                "type": "transfer",
                "status": "completed",
                "source_account_id": "a",
                "destination_account_id": "b",
                "amount": {"amount": "1.00", "currency": "DB"},
                "created_at": "",
                "completed_at": "",
            },
        )

    client = HorizonClient("https://example.test", "hbk_abc_secret")
    client.client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    )
    client.transfer("a", "b", "1.00", idempotency_key="persisted-key-0001")
    assert seen["key"] == "persisted-key-0001"


def test_error_does_not_become_a_silent_retry() -> None:
    client = HorizonClient("https://example.test", "hbk_abc_secret")
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={
                    "error": "unavailable",
                    "code": "core_unavailable",
                    "request_id": "r",
                },
            )
        ),
        base_url="https://example.test",
    )
    with pytest.raises(HorizonError) as error:
        client.transfer("a", "b", "1.00", idempotency_key="persisted-key-0001")
    assert error.value.status_code == 503
