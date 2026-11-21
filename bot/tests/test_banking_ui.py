from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from bot.app.interactions import (
    MainMenu,
    PrivateView,
    TransferConfirmation,
    account_by_name,
    find_recipient,
    transfer_form,
)
from bot.app.presentation import amount_input, money


@pytest.mark.parametrize(
    "value,expected", [("125,50", "125.50"), ("1 250,5", "1250.50"), ("1", "1.00")]
)
def test_russian_amounts_are_exact_strings(value, expected):
    assert amount_input(value) == expected
    assert money(expected).endswith(" ДБ")


@pytest.mark.parametrize("value", ["0", "1.001", "nan", "-1", "1e3", "1,2,3"])
def test_invalid_amounts(value):
    with pytest.raises(ValueError):
        amount_input(value)


@pytest.mark.asyncio
async def test_private_menu_and_uuid_free_modal():
    menu = MainMenu(AsyncMock(), 42)
    assert {item.label for item in menu.children} >= {
        "Баланс",
        "Перевести",
        "История",
        "Мои счета",
        "Профиль",
        "Организации",
    }
    assert max(sum(item.row == row for item in menu.children) for row in range(5)) <= 5
    form = transfer_form(menu.api, 42)
    assert len(form.children) <= 5
    assert all("UUID" not in item.label for item in form.children)
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=43), response=SimpleNamespace(send_message=AsyncMock())
    )
    assert not await menu.interaction_check(ctx)
    assert ctx.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_default_balance_uses_primary_not_first_page():
    api = AsyncMock()
    api.get.return_value = {"id": "primary"}
    assert await account_by_name(api, 42) == {"id": "primary"}
    api.get.assert_awaited_once_with("/accounts/primary", 42)


@pytest.mark.asyncio
async def test_ambiguous_search_always_requires_selection_before_quote():
    api = AsyncMock()
    api.get.return_value = {
        "items": [
            {
                "id": "a",
                "display_name": "Друг",
                "bank_username": "friend_a",
                "public_number": "HB-1",
            },
            {
                "id": "b",
                "display_name": "Друг",
                "bank_username": "friend_b",
                "public_number": "HB-2",
            },
        ]
    }
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await find_recipient(api, ctx, "Друг", "1.00")
    api.post.assert_not_awaited()
    view = ctx.followup.send.call_args.kwargs["view"]
    assert isinstance(view, PrivateView)
    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    assert len(select.options) == 2
    assert ctx.followup.send.call_args.kwargs["ephemeral"]


@pytest.mark.asyncio
async def test_confirmation_uses_server_key_after_lost_response():
    api = AsyncMock()
    view = TransferConfirmation(
        api, 42, {"id": "quote-id", "idempotency_key": "server-key"}
    )
    assert view.state.idempotency_key == "server-key"
    api.post.side_effect = [OSError("lost"), {"id": "operation"}]

    async def send(key):
        return (await api.post("/transfer-quotes/quote-id/confirm", 42, {}, key))["id"]

    with pytest.raises(OSError):
        await view.state.execute(42, send)
    assert await view.state.execute(42, send) == "operation"
    assert [call.args[-1] for call in api.post.call_args_list] == [
        "server-key",
        "server-key",
    ]


@pytest.mark.asyncio
async def test_all_commands_preserved_russian_default_optional_balance_history():
    from bot.app.main import bot

    commands = {item.name: item for item in bot.tree.get_commands()}
    assert len(commands) == 15
    for name in ["balance", "history", "transfer", "operation"]:
        assert not any(item.required for item in commands[name].parameters)
    assert all(
        any("а" <= char <= "я" for char in item.description)
        for item in commands.values()
    )
    await bot.api.close()
