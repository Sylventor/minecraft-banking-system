from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx

from .models import AccountBalance, Operation


class HorizonError(RuntimeError):
    def __init__(
        self, status_code: int, code: str, message: str, request_id: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id


class HorizonClient:
    """Synchronous REST client. Financial writes are never automatically retried."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 10) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> HorizonClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: object) -> dict:
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            data = (
                response.json()
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else {}
            )
            raise HorizonError(
                response.status_code,
                data.get("code", "http_error"),
                data.get("error", "request failed"),
                data.get("request_id", ""),
            )
        return {} if response.status_code == 204 else response.json()

    def balance(self, account_id: str) -> AccountBalance:
        return AccountBalance.model_validate(
            self._request("GET", f"/accounts/{account_id}/balance")
        )

    def operation(self, operation_id: str) -> Operation:
        return Operation.model_validate(
            self._request("GET", f"/operations/{operation_id}")
        )

    def history(self, account_id: str, limit: int = 100) -> list[Operation]:
        return [
            Operation.model_validate(item)
            for item in self._request(
                "GET",
                f"/accounts/{account_id}/operations",
                params={"limit": min(max(limit, 1), 100)},
            )["items"]
        ]

    def transfer(
        self,
        source_account_id: str,
        destination_account_id: str,
        amount: str,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> Operation:
        """Caller may persist a key for a manual retry after a timeout; no retry occurs here."""
        key = idempotency_key or str(uuid.uuid4()) + str(uuid.uuid4())
        data = self._request(
            "POST",
            "/transfers",
            headers={"Idempotency-Key": key},
            json={
                "source_account_id": source_account_id,
                "destination_account_id": destination_account_id,
                "amount": amount,
                "reason": reason,
            },
        )
        return Operation.model_validate(data)

    def iter_history(self, account_id: str) -> Iterator[Operation]:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self._request(
                "GET", f"/accounts/{account_id}/operations", params=params
            )
            yield from (Operation.model_validate(item) for item in page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if cursor in seen:
                raise HorizonError(
                    502, "invalid_pagination", "server returned a repeated cursor"
                )
            seen.add(cursor)
