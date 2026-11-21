"""Classify coordinator replies without logging codes, tokens or response bodies."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

SAFE_REASONS = frozenset(
    {
        "invalid_request",
        "player_unavailable",
        "identity_changed",
        "receipt_timeout",
        "player_not_found",
        "master_unavailable",
        "delivery_not_configured",
        "lease_unavailable",
        "send_failed",
        "delivery_unavailable",
    }
)


def inspect_delivery(response: httpx.Response, expected_mode: str) -> tuple[dict[str, Any] | None, str]:
    try:
        data = response.json()
    except ValueError:
        return None, "invalid_response"
    if not isinstance(data, dict):
        return None, "invalid_response"
    if not response.is_success:
        reason = data.get("reason")
        return None, reason if isinstance(
            reason, str
        ) and reason in SAFE_REASONS else f"coordinator_http_{response.status_code}"
    if data.get("delivered") is not True:
        return None, "delivery_not_confirmed"
    if data.get("authentication") != expected_mode:
        return None, "identity_mode_mismatch"
    try:
        if not isinstance(data.get("player_uuid"), str):
            return None, "invalid_identity"
        uuid.UUID(data["player_uuid"])
    except ValueError:
        return None, "invalid_identity"
    return data, "confirmed"
