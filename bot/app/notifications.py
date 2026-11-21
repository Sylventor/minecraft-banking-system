"""DM delivery worker in the existing bot, independent of command handlers."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import discord
import httpx

from .api import HorizonApi
from .presentation import escape, money

log = logging.getLogger(__name__)


def notification_embed(payload: dict[str, Any]) -> discord.Embed:
    security = payload.get("type", "").startswith(("permission.", "account."))
    incoming = payload.get("direction") == "incoming"
    failed = payload.get("status") in {"failed", "reversed"}
    color = (
        0xE16B72
        if failed
        else 0xF1BE62
        if security
        else 0x57C5A0
        if incoming
        else 0x699EEB
    )
    icon = "⚠" if failed else "🔒" if security else "↙" if incoming else "↗"
    card = discord.Embed(
        title=(icon + " Banking System — " + str(payload.get("title", "Уведомление")))[
            :256
        ],
        description=escape(str(payload.get("body", "")))[:2000],
        color=color,
    )
    if payload.get("amount"):
        sign = "+" if incoming else "−"
        if (
            failed
            or payload.get("direction") == "internal"
            or payload.get("type") in {"hold", "release_hold"}
            or payload.get("payment_request_id")
            or payload.get("type")
            in {"exchange.buy", "exchange.sell", "exchange.ipo_buy"}
        ):
            sign = ""
        card.add_field(
            name="Сумма", value=sign + money(str(payload["amount"])), inline=True
        )
    if payload.get("payment_request_id"):
        card.add_field(
            name="Организация / получатель",
            value=escape(str(payload.get("merchant", "Banking System")))[:200],
        )
        if payload.get("expires_at"):
            card.add_field(name="Срок действия", value=str(payload["expires_at"])[:60])
        # This URL is built by the API from PUBLIC_ORIGIN and a validated UUID,
        # never from merchant metadata. Opening it still requires a user session.
        card.url = payload.get("url")
    if payload.get("type", "").startswith("payroll."):
        if payload.get("organization"):
            card.add_field(
                name="Организация", value=escape(str(payload["organization"]))[:200]
            )
        card.url = payload.get("url")
    if payload.get("type", "").startswith("exchange."):
        card.url = payload.get("url")
        for key, label in (
            ("ticker", "Тикер"),
            ("quantity", "Количество акций"),
            ("price", "Цена, ДБ"),
        ):
            if payload.get(key) is not None:
                card.add_field(
                    name=label, value=escape(str(payload[key]))[:200], inline=True
                )
    if payload.get("counterparty"):
        card.add_field(
            name="Отправитель" if incoming else "Получатель",
            value=escape(str(payload["counterparty"]))[:200],
            inline=True,
        )
    if payload.get("account"):
        card.add_field(
            name="Счёт", value=escape(str(payload["account"]))[:100], inline=True
        )
    if payload.get("operation_id"):
        card.add_field(
            name="Операция", value="#" + str(payload["operation_id"]), inline=False
        )
    if payload.get("public_number"):
        card.add_field(
            name="Реквизиты", value=str(payload["public_number"])[:40], inline=True
        )
    if payload.get("type") == "account.status":
        card.add_field(
            name="Статус",
            value={"active": "Активен", "frozen": "Заморожен", "closed": "Закрыт"}.get(
                payload.get("status"), "Изменён"
            ),
            inline=True,
        )
    if payload.get("type", "").startswith("organization.") and payload.get("role"):
        card.add_field(
            name="Роль",
            value={
                "owner": "Владелец",
                "administrator": "Администратор",
                "member": "Участник",
            }.get(payload["role"], "Изменена"),
            inline=True,
        )
    if payload.get("date"):
        try:
            card.timestamp = datetime.fromisoformat(str(payload["date"]))
        except ValueError:
            pass
    card.set_footer(text="Banking System · Настройки доставки в личном кабинете")
    return card


class NotificationWorker:
    def __init__(self, client: discord.Client, api: HorizonApi) -> None:
        self.client, self.api = client, api

    async def endpoint(self, path: str, payload: dict[str, Any]) -> Any:
        response = await self.api.client.post(
            "internal/notifications/" + path,
            headers={"X-Horizon-Bot-Token": self.api.token},
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def deliver(self, item: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"lease_token": item["lease_token"]}
        try:
            async with asyncio.timeout(30):
                user = self.client.get_user(
                    int(item["discord_id"])
                ) or await self.client.fetch_user(int(item["discord_id"]))
                # discord.py sets enforce_nonce=true for nonempty nonce. Replays use
                # the same nonce; Discord deduplicates recent ambiguous deliveries.
                message = await user.send(
                    embed=notification_embed(item["payload"]),
                    nonce=item["nonce"],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return {**result, "outcome": "sent", "message_id": str(message.id)}
        except discord.Forbidden:
            return {**result, "outcome": "failed", "error": "dm_closed"}
        except discord.NotFound:
            return {**result, "outcome": "failed", "error": "recipient_unavailable"}
        except discord.HTTPException as error:
            # The existing discord.py HTTP client observes per-route/global limits.
            return {
                **result,
                "outcome": "retry",
                "error": "rate_limited"
                if error.status == 429
                else "discord_unavailable",
                "retry_after": 60 if error.status == 429 else 0,
            }
        except (TimeoutError, OSError):
            return {**result, "outcome": "retry", "error": "delivery_unknown"}

    async def run_once(self) -> bool:
        batch = await self.endpoint("claim", {})
        for item in batch["items"]:
            result = await self.deliver(item)
            await self.endpoint(str(item["id"]) + "/result", result)
        return bool(batch["items"])

    async def run(self) -> None:
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            try:
                active = await self.run_once()
                await asyncio.sleep(0.35 if active else 5)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, discord.DiscordException, OSError, ValueError):
                log.warning("notification_worker_retry")
                await asyncio.sleep(5)
