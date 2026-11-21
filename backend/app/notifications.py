"""Durable notification relay. Discord is never called from a financial request."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import suppress
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .database import SessionLocal

SERVICE_ACTOR = "00000000-0000-0000-0000-000000000022"
log = structlog.get_logger()


async def enqueue(
    session: AsyncSession,
    notification: uuid.UUID,
    user: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
    category: str | None = None,
) -> None:
    category = category or ("security" if kind == "security" else "incoming" if kind == "transfer" else "organizations")
    await session.execute(
        text("""INSERT INTO auth.discord_outbox(id,notification_id,user_id,category,critical,payload)
      VALUES(:id,:notification,:user,:category,:critical,CAST(:payload AS jsonb)) ON CONFLICT(notification_id) DO NOTHING"""),
        {
            "id": uuid.uuid4(),
            "notification": notification,
            "user": user,
            "category": category,
            "critical": kind == "security",
            "payload": json.dumps({"title": title[:240], "body": body[:1500], **(payload or {})}),
        },
    )


async def relay(session: AsyncSession, core: Any) -> int:
    batch = await core.notification_events()
    acknowledged = []
    for event in batch["items"]:
        acknowledged.append(event["id"])
        user = uuid.UUID(event["principal_id"])
        if not await session.scalar(
            text(
                "SELECT id FROM auth.application_users WHERE id=:id AND erased_at IS NULL "
                "AND processing_restricted_at IS NULL FOR SHARE"
            ),
            {"id": user},
        ):
            # Core may also serve service principals without an interactive profile.
            continue
        payload = event["payload"]
        accounts = payload.get("visible_accounts") or []
        if accounts:
            payload = {**payload, "categories": sorted({a["category"] for a in accounts})}
        if len(accounts) > 1:
            payload = {**payload, "direction": "internal", "account": " → ".join(a["name"] for a in accounts)}
        titles = {
            "transfer": "Перевод",
            "payment": "Оплата",
            "refund": "Возврат средств",
            "issue": "Зачисление администратором",
            "hold": "Средства заблокированы",
            "release_hold": "Средства разблокированы",
            "capture_hold": "Списание резерва",
            "account.created": "Банковский счёт создан",
            "account.status": "Статус счёта изменён",
            "organization.invited": "Приглашение в организацию",
            "organization.role": "Роль организации изменена",
            "organization.removed": "Доступ организации отозван",
            "permission.changed": "Финансовые права изменены",
            "payment_request.pending": "Запрос на оплату",
            "payment_request.paid": "Запрос оплачен",
            "payment_request.declined": "Запрос отклонён",
            "payment_request.cancelled": "Запрос отменён",
            "payment_request.expired": "Срок оплаты истёк",
            "payroll.salary": "Зарплата",
            "payroll.pending_approval": "Ведомость ожидает утверждения",
            "payroll.completed": "Зарплатная ведомость выполнена",
            "payroll.partially_completed": "Зарплатная ведомость выполнена частично",
            "payroll.failed": "Зарплатные выплаты не выполнены",
            "payroll.execution_error": "Ошибка запуска зарплат: проверьте баланс и права",
            "payroll.schedule_paused": "Зарплатное расписание приостановлено",
            "exchange.buy": "Покупка акций",
            "exchange.sell": "Продажа акций",
            "exchange.ipo_buy": "Покупка акций на IPO",
            "exchange.order_partial": "Заявка исполнена частично",
            "exchange.order_filled": "Заявка исполнена полностью",
            "exchange.order_open": "Биржевая заявка размещена",
            "exchange.order_cancelled": "Биржевая заявка отменена",
            "exchange.order_expired": "Срок биржевой заявки истёк",
            "exchange.dividend": "Начислены дивиденды",
            "exchange.listing_approved": "Выпуск акций одобрен",
            "exchange.listing_suspended": "Торги приостановлены",
            "exchange.listing_active": "Торги возобновлены",
            "exchange.listing_rejected": "Выпуск акций отклонён",
            "exchange.listing_delisted": "Акции сняты с торгов",
        }
        title = titles.get(payload.get("type", ""), "Изменение банковского счёта")
        if payload.get("type") == "transfer":
            title = (
                "Перевод между вашими счетами"
                if payload.get("direction") == "internal"
                else "Входящий перевод"
                if payload.get("direction") == "incoming"
                else "Исходящий перевод"
            )
        if payload.get("status") in {"failed", "reversed"}:
            title = "Операция отклонена" if payload["status"] == "failed" else "Операция отменена"
        other_key = "source_owner_id" if payload.get("direction") == "incoming" else "destination_owner_id"
        other = payload.get(other_key)
        counterparty = (
            await session.scalar(
                text("SELECT COALESCE(bank_username,display_name) FROM auth.application_users WHERE id=:id"),
                {"id": uuid.UUID(other)},
            )
            if other
            else None
        )
        payload = {
            **payload,
            "counterparty": counterparty or ("Banking System" if payload.get("type") == "issue" else "Банковский счёт"),
        }
        body = (
            (payload.get("amount", "") + " ДБ · " + payload.get("account", ""))
            if payload.get("amount")
            else payload.get("account") or payload.get("organization") or "Проверьте изменения в личном кабинете."
        )
        if payload.get("payment_request_id"):
            from .config import get_settings

            ident_request = str(uuid.UUID(payload["payment_request_id"]))
            payload = {**payload, "url": str(get_settings().public_origin).rstrip("/") + "/payments/" + ident_request}
            body = str(payload.get("merchant", "Banking System")) + " · " + str(payload.get("description", ""))
        if payload.get("type", "").startswith("payroll."):
            from .config import get_settings

            origin = str(get_settings().public_origin).rstrip("/")
            payload = {
                **payload,
                "url": origin + "/#/history"
                if payload["type"] == "payroll.salary"
                else origin
                + "/#/organizations?tab=payroll&organization_id="
                + str(uuid.UUID(payload["organization_id"])),
            }
            body = str(payload.get("organization", "Banking System")) + " · " + str(payload.get("description", title))
            if payload["type"] == "payroll.salary":
                payload["counterparty"] = payload.get("organization", "Организация")
        if payload.get("type", "").startswith("exchange."):
            from .config import get_settings

            payload = {**payload, "url": str(get_settings().public_origin).rstrip("/") + "/exchange"}
            body = " · ".join(
                str(v)
                for v in (
                    payload.get("ticker") or payload.get("name"),
                    payload.get("quantity"),
                    payload.get("amount"),
                    payload.get("status"),
                )
                if v is not None
            )
            if payload.get("type") in {"exchange.buy", "exchange.ipo_buy", "exchange.dividend"}:
                payload["direction"] = "incoming"
            if payload.get("type") == "exchange.sell":
                payload["direction"] = "outgoing"
        operation = payload.get("operation_id")
        # Reuse 2.1's in-app operation key. Internal own-account transfers also produce one DM.
        key = "operation:" + operation if operation else "core:" + event["event_key"]
        kind = "transfer" if operation else "security" if event["category"] == "security" else "organization"
        ident = uuid.uuid4()
        await session.execute(
            text("""INSERT INTO auth.user_notifications(id,user_id,event_key,kind,title,body,operation_id,created_at)
          VALUES(:id,:user,:key,:kind,:title,:body,CAST(:operation AS uuid),CAST(:created AS timestamptz))
          ON CONFLICT(user_id,event_key) DO NOTHING"""),
            {
                "id": ident,
                "user": user,
                "key": key,
                "kind": kind,
                "title": title,
                "body": body,
                "operation": operation,
                "created": datetime.fromisoformat(str(event["created_at"])),
            },
        )
        ident = await session.scalar(
            text("SELECT id FROM auth.user_notifications WHERE user_id=:user AND event_key=:key"),
            {"user": user, "key": key},
        )
        await enqueue(session, ident, user, kind, title, body, payload, event["category"])
    await session.commit()
    if acknowledged:
        await core.acknowledge_notification_events(acknowledged)
    return len(acknowledged)


async def relay_loop(provider: Any) -> None:
    while True:
        try:
            async with SessionLocal() as session:
                count = await relay(session, await provider.get())
            await asyncio.sleep(0.1 if count == 100 else 2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("notification_relay_retry", service="api")
            await asyncio.sleep(5)


async def stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
