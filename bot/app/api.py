from __future__ import annotations

import uuid
from typing import Any

import httpx


class HorizonApi:
    def __init__(self, url: str, token: str) -> None:
        self.client = httpx.AsyncClient(base_url=url.rstrip("/") + "/", timeout=8)
        self.token = token

    def headers(self, user_id: int, key: str | None = None) -> dict[str, str]:
        result = {
            "X-Horizon-Bot-Token": self.token,
            "X-Discord-User-ID": str(user_id),
            "X-Correlation-ID": str(uuid.uuid4()),
            "Accept-Language": "ru",
        }
        if key:
            result["Idempotency-Key"] = key
        return result

    async def get(self, path: str, user_id: int) -> Any:
        response = await self.client.get(
            path.lstrip("/"), headers=self.headers(user_id)
        )
        response.raise_for_status()
        return response.json()

    async def post(
        self, path: str, user_id: int, payload: dict[str, Any], key: str | None = None
    ) -> Any:
        response = await self.client.post(
            path.lstrip("/"), headers=self.headers(user_id, key), json=payload
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.client.aclose()

    async def put(self, path: str, user_id: int, payload: dict[str, Any]) -> Any:
        response = await self.client.put(
            path.lstrip("/"), headers=self.headers(user_id), json=payload
        )
        response.raise_for_status()
        return response.json()

    async def patch(self, path: str, user_id: int, payload: dict[str, Any]) -> Any:
        response = await self.client.patch(
            path.lstrip("/"), headers=self.headers(user_id), json=payload
        )
        response.raise_for_status()
        return response.json()

    async def delete(self, path: str, user_id: int) -> None:
        response = await self.client.delete(
            path.lstrip("/"), headers=self.headers(user_id)
        )
        response.raise_for_status()
