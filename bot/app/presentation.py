"""Russian presentation only. No monetary calculations or financial rules live in the bot."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import discord

BLUE = 0x7FACED


def escape(value: object) -> str:
    return discord.utils.escape_markdown(str(value)).replace("@", "＠")


def amount_input(value: str) -> str:
    value = (
        value.strip()
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(",", ".")
    )
    if not re.fullmatch(r"(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?", value):
        raise ValueError(
            "Введите положительную сумму, например 125,50. Не более двух знаков после запятой."
        )
    whole, _, fraction = value.partition(".")
    canonical = whole + "." + fraction.ljust(2, "0")
    if canonical == "0.00":
        raise ValueError("Сумма перевода должна быть больше нуля")
    return canonical


def money(value: str) -> str:
    whole, _, fraction = value.partition(".")
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return " ".join(reversed(groups)) + "," + fraction.ljust(2, "0") + " ДБ"


def date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M UTC")
    except ValueError:
        return value[:30]


def embed(title: str, description: str = "") -> discord.Embed:
    result = discord.Embed(title=title, description=description, color=BLUE)
    result.set_footer(text="Banking System · Ваш мир. Ваш банк.")
    return result


def menu_embed(data: dict[str, Any]) -> discord.Embed:
    profile = data["profile"]
    primary = data["primary_account"]
    result = embed(
        "Banking System", "Добро пожаловать, **" + escape(profile["display_name"]) + "**"
    )
    result.add_field(
        name="Доступно на основном счёте",
        value="**" + money(primary["available"]["amount"]) + "**",
        inline=False,
    )
    result.add_field(name="Счёт", value=escape(primary["name"]), inline=True)
    result.add_field(
        name="Заблокировано", value=money(primary["held"]["amount"]), inline=True
    )
    result.add_field(
        name="Банковское имя",
        value="@" + escape(profile["bank_username"] or "—"),
        inline=True,
    )
    minecraft = profile.get("minecraft")
    if minecraft:
        result.add_field(
            name="Minecraft · подтверждён сервером",
            value=escape(minecraft["nickname"]),
            inline=False,
        )
    return result


def account_embed(account: dict[str, Any]) -> discord.Embed:
    result = embed(
        escape(account["name"]),
        "Основной личный счёт"
        if account["is_primary"]
        else "Счёт организации"
        if account["organization_id"]
        else "Личный счёт",
    )
    result.add_field(
        name="Доступно",
        value="**" + money(account["available"]["amount"]) + "**",
        inline=False,
    )
    result.add_field(
        name="Баланс", value=money(account["balance"]["amount"]), inline=True
    )
    result.add_field(
        name="Заблокировано", value=money(account["held"]["amount"]), inline=True
    )
    result.add_field(
        name="Публичные реквизиты",
        value="`" + account["public_number"] + "`",
        inline=False,
    )
    result.add_field(
        name="Статус",
        value={"active": "Активен", "frozen": "Заблокирован", "closed": "Закрыт"}.get(
            account["status"], "Недоступен"
        ),
        inline=False,
    )
    return result


def quote_embed(quote: dict[str, Any]) -> discord.Embed:
    recipient = quote["recipient"]
    result = embed(
        "Проверьте перевод", "Деньги спишутся только после вашего подтверждения."
    )
    result.add_field(
        name="Получатель",
        value="**"
        + escape(recipient["display_name"])
        + "**\n"
        + (
            "@" + escape(recipient["bank_username"])
            if recipient.get("bank_username")
            else recipient["public_number"]
        ),
        inline=False,
    )
    if recipient.get("minecraft_nickname"):
        result.add_field(
            name="Minecraft", value=escape(recipient["minecraft_nickname"]), inline=True
        )
    result.add_field(
        name="Счёт получателя",
        value="`" + recipient["public_number"] + "`",
        inline=False,
    )
    result.add_field(
        name="Со счёта", value=escape(quote["source"]["name"]), inline=False
    )
    result.add_field(name="Сумма", value=money(quote["amount"]["amount"]), inline=True)
    result.add_field(name="Комиссия", value=money(quote["fee"]["amount"]), inline=True)
    result.add_field(
        name="Итого", value="**" + money(quote["total"]["amount"]) + "**", inline=True
    )
    if quote.get("reason"):
        result.add_field(
            name="Комментарий", value=escape(quote["reason"]), inline=False
        )
    result.add_field(
        name="Подтверждение до", value=date(quote["expires_at"]), inline=False
    )
    return result


def receipt_embed(operation: dict[str, Any]) -> discord.Embed:
    result = embed(
        "Перевод выполнен ✓"
        if operation["type"] == "transfer"
        else "Квитанция операции",
        money(operation["amount"]["amount"]),
    )
    result.add_field(
        name="Отправитель",
        value=escape(
            operation.get("sender") or operation.get("source_name") or "Banking System"
        ),
        inline=True,
    )
    result.add_field(
        name="Получатель",
        value=escape(
            operation.get("recipient")
            or operation.get("destination_name")
            or "Банковский счёт"
        ),
        inline=True,
    )
    result.add_field(name="Дата", value=date(operation["created_at"]), inline=False)
    result.add_field(
        name="Комиссия",
        value=money(operation.get("fee", {}).get("amount", "0.00")),
        inline=True,
    )
    result.add_field(
        name="Статус",
        value="Выполнено"
        if operation["status"] == "completed"
        else escape(operation["status"]),
        inline=True,
    )
    if operation.get("reason"):
        result.add_field(
            name="Комментарий", value=escape(operation["reason"]), inline=False
        )
    result.add_field(
        name="Номер операции", value="`" + operation["id"] + "`", inline=False
    )
    return result
