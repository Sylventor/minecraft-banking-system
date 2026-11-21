from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from bot.app.notifications import NotificationWorker, notification_embed


def test_embed_uses_current_currency_and_escapes_user_content():
    card = notification_embed(
        {
            "title": "Входящий перевод",
            "body": "**unsafe**",
            "amount": "150.00",
            "direction": "incoming",
            "account": "Основной",
            "counterparty": "@everyone",
            "operation_id": "test-operation",
        }
    )
    assert card.fields[0].value == "+150,00 ДБ"
    assert "\\*" in card.description
    assert "test-operation" in str(card.to_dict())


def test_payment_request_embed_has_unsigned_amount_and_authenticated_link():
    card = notification_embed(
        {
            "type": "payment_request.pending",
            "title": "Запрос на оплату",
            "body": "Shop · Item",
            "amount": "150.00",
            "merchant": "Shop",
            "payment_request_id": "request-id",
            "expires_at": "2026-10-22T18:30:00Z",
            "url": "https://bank.example/payments/request-id",
        }
    )
    assert card.fields[0].value == "150,00 ДБ"
    assert card.url == "https://bank.example/payments/request-id"
    assert any(f.name == "Срок действия" for f in card.fields)


def test_salary_embed_has_employer_amount_and_history_link():
    card = notification_embed(
        {
            "type": "payroll.salary",
            "title": "Зарплата",
            "body": "Employer · October",
            "organization": "Employer",
            "amount": "100.00",
            "direction": "incoming",
            "status": "completed",
            "operation_id": "operation",
            "url": "https://bank.example/#/history",
        }
    )
    assert card.fields[0].value == "+100,00 ДБ"
    assert card.url == "https://bank.example/#/history"
    assert any(f.name == "Организация" and f.value == "Employer" for f in card.fields)


def test_exchange_embed_contains_confirmed_ticker_quantity_and_cost():
    card = notification_embed(
        {
            "type": "exchange.buy",
            "title": "Покупка акций",
            "body": "XCON",
            "ticker": "XCON",
            "quantity": 4,
            "price": "12.00",
            "amount": "48.00",
            "status": "completed",
            "url": "https://bank.example/exchange",
        }
    )
    assert card.url == "https://bank.example/exchange"
    assert any(f.name == "Тикер" and f.value == "XCON" for f in card.fields)
    assert any(f.name == "Количество акций" and f.value == "4" for f in card.fields)


@pytest.mark.asyncio
async def test_closed_dms_do_not_crash_worker_and_nonce_is_stable():
    user = SimpleNamespace(
        send=AsyncMock(
            side_effect=discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "DM closed"
            )
        )
    )
    bot = Mock()
    bot.get_user.return_value = user
    worker = NotificationWorker(bot, Mock())
    item = {
        "id": "id",
        "lease_token": "token",
        "discord_id": "123",
        "nonce": "42",
        "payload": {"title": "Тест"},
    }
    result = await worker.deliver(item)
    assert result["outcome"] == "failed" and result["error"] == "dm_closed"
    user.send = AsyncMock(return_value=SimpleNamespace(id=55))
    assert (await worker.deliver(item))["message_id"] == "55"
    assert user.send.call_args.kwargs["nonce"] == "42"


@pytest.mark.asyncio
async def test_discord_timeout_is_retryable():
    bot = Mock()
    bot.get_user.return_value = SimpleNamespace(
        send=AsyncMock(side_effect=TimeoutError)
    )
    result = await NotificationWorker(bot, Mock()).deliver(
        {"lease_token": "token", "discord_id": "123", "nonce": "42", "payload": {}}
    )
    assert result["outcome"] == "retry" and result["error"] == "delivery_unknown"
