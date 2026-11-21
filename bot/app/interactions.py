"""Private Discord workflows; all authority and money stay in REST/core."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import discord
import httpx

from .api import HorizonApi
from .confirmation import Confirmation
from .presentation import (
    account_embed,
    amount_input,
    embed,
    escape,
    menu_embed,
    money,
    quote_embed,
    receipt_embed,
)


async def error_message(ctx: discord.Interaction, error: Exception) -> None:
    detail = "Банк временно недоступен. Если вы подтверждали перевод, повторите то же подтверждение в /menu, не создавая новый перевод."
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = str(
                error.response.json().get("error", "Не удалось выполнить действие")
            )
        except (ValueError, AttributeError):
            detail = "Не удалось выполнить действие. Попробуйте позже."
    elif isinstance(error, (ValueError, PermissionError, TimeoutError)):
        detail = str(error)
    if ctx.response.is_done():
        await ctx.followup.send(escape(detail)[:1900], ephemeral=True)
    else:
        await ctx.response.send_message(escape(detail)[:1900], ephemeral=True)


async def show(
    ctx: discord.Interaction, card: discord.Embed, view: discord.ui.View | None = None
) -> None:
    options = {"embed": card, "ephemeral": True}
    if view is not None:
        options["view"] = view
    if ctx.response.is_done():
        await ctx.followup.send(**options)
    else:
        await ctx.response.send_message(**options)


class PrivateView(discord.ui.View):
    def __init__(self, api: HorizonApi, user_id: int) -> None:
        super().__init__(timeout=600)
        self.api, self.user_id = api, user_id

    async def interaction_check(self, ctx: discord.Interaction) -> bool:
        if ctx.user.id == self.user_id:
            return True
        await ctx.response.send_message(
            "Это меню принадлежит другому пользователю.", ephemeral=True
        )
        return False

    async def on_error(
        self, ctx: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await error_message(ctx, error)

    def action(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        primary: bool = False,
        row: int | None = None,
    ) -> None:
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary
            if primary
            else discord.ButtonStyle.secondary,
            row=row,
        )
        button.callback = callback
        self.add_item(button)


class Choice(discord.ui.Select):
    def __init__(
        self,
        placeholder: str,
        options: list[discord.SelectOption],
        choose: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(placeholder=placeholder, options=options[:25])
        self.choose = choose

    async def callback(self, ctx: discord.Interaction) -> None:
        await self.choose(ctx, self.values[0])


class Form(discord.ui.Modal):
    def __init__(
        self,
        title: str,
        user_id: int,
        fields: list[tuple[str, int, bool]],
        submit: Callable[[discord.Interaction, list[str]], Awaitable[None]],
    ) -> None:
        super().__init__(title=title, timeout=300)
        self.user_id, self.submit = user_id, submit
        for label, limit, required in fields:
            self.add_item(
                discord.ui.TextInput(label=label, max_length=limit, required=required)
            )

    async def on_submit(self, ctx: discord.Interaction) -> None:
        try:
            if ctx.user.id != self.user_id:
                raise PermissionError("Эта форма принадлежит другому пользователю")
            await self.submit(
                ctx,
                [
                    str(item.value)
                    for item in self.children
                    if isinstance(item, discord.ui.TextInput)
                ],
            )
        except Exception as error:
            await error_message(ctx, error)


async def account_by_name(
    api: HorizonApi, user_id: int, name: str = ""
) -> dict[str, Any]:
    if not name:
        return await api.get("/accounts/primary", user_id)
    items = (await api.get("/dashboard", user_id))["accounts"]
    matches = [
        item
        for item in items
        if name.casefold()
        in (item["name"].casefold(), item["public_number"].casefold(), item["id"])
    ]
    if len(matches) != 1:
        raise ValueError(
            "Счёт не найден или названия совпадают. Выберите счёт в /accounts или укажите публичные реквизиты HB-."
        )
    return matches[0]


async def open_menu(api: HorizonApi, ctx: discord.Interaction) -> None:
    await ctx.response.defer(ephemeral=True)
    await show(
        ctx,
        menu_embed(await api.get("/dashboard", ctx.user.id)),
        MainMenu(api, ctx.user.id),
    )


class MainMenu(PrivateView):
    def __init__(self, api: HorizonApi, user_id: int) -> None:
        super().__init__(api, user_id)
        for label, callback, row in [
            ("Баланс", self.balance, 0),
            ("Перевести", self.transfer, 0),
            ("История", self.history, 0),
            ("Мои счета", self.accounts, 1),
            ("Профиль", self.profile, 1),
            ("Организации", self.organizations, 1),
            ("Незавершённые переводы", self.drafts, 2),
        ]:
            self.action(label, callback, row=row, primary=label == "Перевести")

    async def balance(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        account = await self.api.get("/accounts/primary", self.user_id)
        await show(
            ctx, account_embed(account), AccountActions(self.api, self.user_id, account)
        )

    async def transfer(self, ctx: discord.Interaction) -> None:
        await ctx.response.send_modal(transfer_form(self.api, self.user_id))

    async def history(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        account = await self.api.get("/accounts/primary", self.user_id)
        await history_page(self.api, ctx, account["id"])

    async def accounts(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        await accounts_page(self.api, ctx)

    async def profile(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        await profile_page(self.api, ctx)

    async def organizations(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        await organizations_page(self.api, ctx)

    async def drafts(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        items = await self.api.get("/transfer-drafts", self.user_id)
        view = PrivateView(self.api, self.user_id)
        if items:

            async def choose(click: discord.Interaction, value: str) -> None:
                await click.response.defer(ephemeral=True)
                quote = await self.api.get("/transfer-quotes/" + value, self.user_id)
                await show(
                    click,
                    quote_embed(quote),
                    TransferConfirmation(self.api, self.user_id, quote, resumed=True),
                )

            view.add_item(
                Choice(
                    "Выберите перевод для проверки",
                    [
                        discord.SelectOption(
                            label=(item["recipient"] + " · " + money(item["amount"]))[
                                :100
                            ],
                            value=item["id"],
                            description=str(item["created_at"])[:100],
                        )
                        for item in items
                    ],
                    choose,
                )
            )
        await show(
            ctx,
            embed(
                "Незавершённые переводы",
                "Возобновляется исходное подтверждение. Истёкший перевод может вернуть только уже выполненную квитанцию."
                if items
                else "Незавершённых переводов нет. Квитанции — в истории.",
            ),
            view,
        )


class AccountActions(PrivateView):
    def __init__(self, api: HorizonApi, user_id: int, account: dict[str, Any]) -> None:
        super().__init__(api, user_id)
        self.account = account
        if "transfer" in account["permissions"]:
            self.action("Перевести", self.transfer, primary=True)
        if "history.read" in account["permissions"]:
            self.action("История", self.history)
        if "account.manage" in account["permissions"]:
            self.action("Переименовать", self.rename)
            if not account["organization_id"] and not account["is_primary"]:
                self.action("Сделать основным", self.primary)

    async def transfer(self, ctx: discord.Interaction) -> None:
        await ctx.response.send_modal(
            transfer_form(self.api, self.user_id, self.account["id"])
        )

    async def history(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        await history_page(self.api, ctx, self.account["id"])

    async def rename(self, ctx: discord.Interaction) -> None:
        async def submit(click: discord.Interaction, fields: list[str]) -> None:
            await click.response.defer(ephemeral=True)
            data = await self.api.patch(
                "/accounts/" + self.account["id"], self.user_id, {"name": fields[0]}
            )
            await show(
                click, account_embed(data), AccountActions(self.api, self.user_id, data)
            )

        await ctx.response.send_modal(
            Form("Название счёта", self.user_id, [("Новое название", 80, True)], submit)
        )

    async def primary(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        data = await self.api.patch(
            "/accounts/" + self.account["id"], self.user_id, {"is_primary": True}
        )
        await show(
            ctx, account_embed(data), AccountActions(self.api, self.user_id, data)
        )


async def accounts_page(
    api: HorizonApi, ctx: discord.Interaction, cursor: str = ""
) -> None:
    data = await api.get(
        "/accounts?"
        + urlencode({"limit": 25, **({"cursor": cursor} if cursor else {})}),
        ctx.user.id,
    )
    view = PrivateView(api, ctx.user.id)
    if data["items"]:

        async def choose(click: discord.Interaction, value: str) -> None:
            await click.response.defer(ephemeral=True)
            account = await api.get("/accounts/" + value + "/balance", click.user.id)
            await show(
                click,
                account_embed(account),
                AccountActions(api, click.user.id, account),
            )

        view.add_item(
            Choice(
                "Выберите счёт",
                [
                    discord.SelectOption(
                        label=(("★ " if item["is_primary"] else "") + item["name"])[
                            :100
                        ],
                        value=item["id"],
                        description=(
                            money(item["available"]["amount"])
                            + " · "
                            + item["public_number"]
                        )[:100],
                    )
                    for item in data["items"]
                ],
                choose,
            )
        )
    if data.get("next_cursor"):

        async def more(click: discord.Interaction) -> None:
            await click.response.defer(ephemeral=True)
            await accounts_page(api, click, data["next_cursor"])

        view.action("Следующие счета", more)
    await show(
        ctx,
        embed(
            "Мои счета",
            "Выберите счёт для реквизитов и действий."
            if data["items"]
            else "Счетов пока нет. Выполните /start.",
        ),
        view,
    )


def transfer_form(api: HorizonApi, user_id: int, source: str | None = None) -> Form:
    async def submit(ctx: discord.Interaction, fields: list[str]) -> None:
        await ctx.response.defer(ephemeral=True)
        await find_recipient(
            api, ctx, fields[0], amount_input(fields[1]), fields[2], source
        )

    return Form(
        "Новый перевод",
        user_id,
        [
            ("Кому: имя банка, MC, Discord или HB-", 80, True),
            ("Сумма, например 125,50", 25, True),
            ("Комментарий", 500, False),
        ],
        submit,
    )


async def find_recipient(
    api: HorizonApi,
    ctx: discord.Interaction,
    query: str,
    amount: str,
    reason: str = "",
    source: str | None = None,
) -> None:
    if query.startswith("user:"):
        items = [
            await api.post(
                "/recipients/resolve", ctx.user.id, {"kind": "user", "value": query[5:]}
            )
        ]
    else:
        result = await api.get(
            "/recipients/search?" + urlencode({"q": query}), ctx.user.id
        )
        items = result["items"]
    if not items:
        raise ValueError(
            "Получатель не найден. Проверьте имя банка, подтверждённый Minecraft-ник, Discord ID или реквизиты HB-."
        )
    view = PrivateView(api, ctx.user.id)

    async def choose(click: discord.Interaction, value: str) -> None:
        await click.response.defer(ephemeral=True)
        recipient = items[int(value)]
        reference = (
            {"kind": "user", "value": recipient["id"]}
            if recipient.get("id") and not query.upper().startswith("HB-")
            else {"kind": "account", "value": recipient["public_number"]}
        )
        payload = {"recipient": reference, "amount": amount, "reason": reason}
        if source:
            payload["source_account_id"] = source
        quote = await api.post("/transfer-quotes", click.user.id, payload)
        await show(
            click, quote_embed(quote), TransferConfirmation(api, click.user.id, quote)
        )

    view.add_item(
        Choice(
            "Обязательно выберите получателя",
            [
                discord.SelectOption(
                    label=(
                        item["display_name"]
                        + (
                            " · @" + item["bank_username"]
                            if item.get("bank_username")
                            else ""
                        )
                    )[:100],
                    value=str(index),
                    description=(
                        (
                            ("MC: " + item["minecraft_nickname"] + " · ")
                            if item.get("minecraft_nickname")
                            else ""
                        )
                        + item["public_number"]
                    )[:100],
                )
                for index, item in enumerate(items)
            ],
            choose,
        )
    )
    await show(
        ctx,
        embed(
            "Кому переводим?",
            "Совпадений: "
            + str(len(items))
            + ". Проверьте банковское имя и реквизиты. Получатель не выбран автоматически.",
        ),
        view,
    )


class TransferConfirmation(PrivateView):
    def __init__(
        self,
        api: HorizonApi,
        user_id: int,
        quote: dict[str, Any],
        *,
        resumed: bool = False,
    ) -> None:
        super().__init__(api, user_id)
        self.quote, self.uncertain = quote, resumed
        self.state = Confirmation(
            user_id, timeout=600, idempotency_key=quote["idempotency_key"]
        )
        self.action(
            "Проверить / подтвердить" if resumed else "Подтвердить перевод",
            self.confirm,
            primary=True,
        )
        self.action("Закрыть" if resumed else "Отмена", self.cancel)

    async def confirm(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        receipt: dict[str, Any] = {}

        async def send(key: str) -> str:
            nonlocal receipt
            self.uncertain = True
            receipt = await self.api.post(
                "/transfer-quotes/" + self.quote["id"] + "/confirm",
                self.user_id,
                {},
                key,
            )
            return str(receipt["id"])

        try:
            operation_id = await self.state.execute(ctx.user.id, send)
            if not receipt:
                receipt = await self.api.get("/receipts/" + operation_id, self.user_id)
            await ctx.edit_original_response(
                content=None, embed=receipt_embed(receipt), view=None
            )
            self.stop()
        except Exception:
            await ctx.edit_original_response(
                content="Результат пока не подтверждён. Повторите ту же кнопку. После перезапуска: /menu → Незавершённые переводы. Закрытие окна не отменяет отправленный запрос.",
                view=self,
            )
            raise

    async def cancel(self, ctx: discord.Interaction) -> None:
        async with self.state.lock:
            if self.state.completed_operation_id:
                await ctx.response.send_message(
                    "Перевод уже выполнен. Квитанция — в истории.", ephemeral=True
                )
                return
            self.state.timeout = 0
            await ctx.response.edit_message(
                content="Окно закрыто. Отправленный запрос нельзя отменить — проверьте историю или незавершённые переводы."
                if self.uncertain
                else "Перевод не отправлен. Окно подтверждения закрыто.",
                embed=None,
                view=None,
            )
            self.stop()


async def history_page(
    api: HorizonApi, ctx: discord.Interaction, account_id: str, cursor: str = ""
) -> None:
    data = await api.get(
        "/activity?"
        + urlencode({"account_id": account_id, "limit": 10, "cursor": cursor}),
        ctx.user.id,
    )
    items = data["items"]
    card, view = (
        embed(
            "История операций",
            "Выберите операцию для квитанции." if items else "Операций пока нет.",
        ),
        PrivateView(api, ctx.user.id),
    )
    labels = {
        "transfer": "Перевод",
        "issue": "Зачисление",
        "refund": "Возврат",
        "payment": "Оплата",
        "hold": "Блокировка",
    }
    for item in items:
        card.add_field(
            name=labels.get(item["type"], "Операция")
            + " · "
            + money(item["amount"]["amount"]),
            value=(
                escape(item.get("sender", "Банк"))
                + " → "
                + escape(item.get("recipient", "Счёт"))
                + "\n"
                + str(item["created_at"])[:19]
            ),
            inline=False,
        )
    if items:

        async def choose(click: discord.Interaction, value: str) -> None:
            await click.response.defer(ephemeral=True)
            await show(
                click, receipt_embed(await api.get("/receipts/" + value, click.user.id))
            )

        view.add_item(
            Choice(
                "Открыть квитанцию",
                [
                    discord.SelectOption(
                        label=(
                            labels.get(item["type"], "Операция")
                            + " · "
                            + money(item["amount"]["amount"])
                        )[:100],
                        description=str(item["created_at"])[:100],
                        value=item["id"],
                    )
                    for item in items
                ],
                choose,
            )
        )
    if data.get("next_cursor"):

        async def more(click: discord.Interaction) -> None:
            await click.response.defer(ephemeral=True)
            await history_page(api, click, account_id, data["next_cursor"])

        view.action("Следующие операции", more)
    await show(ctx, card, view)


async def profile_page(api: HorizonApi, ctx: discord.Interaction) -> None:
    data = await api.get("/me", ctx.user.id)
    card = embed("Мой профиль", escape(data["display_name"]))
    card.add_field(
        name="Банковское имя",
        value="@" + escape(data.get("bank_username") or "Не задано"),
    )
    for key, title in [
        ("discord", "Discord · подтверждён"),
        ("minecraft", "Minecraft · подтверждён сервером"),
    ]:
        if data.get(key):
            card.add_field(
                name=title,
                value=escape(data[key].get("username") or data[key].get("nickname")),
            )
    card.add_field(
        name="Настройки",
        value="Имя, язык, безопасность и уведомления — в настройках на сайте банка.",
        inline=False,
    )
    await show(ctx, card)


async def organizations_page(
    api: HorizonApi,
    ctx: discord.Interaction,
    items: list[dict[str, Any]] | None = None,
    offset: int = 0,
) -> None:
    if items is None:
        items = await api.get("/organizations", ctx.user.id)
    view = PrivateView(api, ctx.user.id)
    if items:

        async def choose(click: discord.Interaction, value: str) -> None:
            await click.response.defer(ephemeral=True)
            await organization_page(api, click, value)

        view.add_item(
            Choice(
                "Выберите организацию",
                [
                    discord.SelectOption(label=item["name"][:100], value=item["id"])
                    for item in items[offset : offset + 25]
                ],
                choose,
            )
        )
        if offset + 25 < len(items):

            async def more(click: discord.Interaction) -> None:
                await click.response.defer(ephemeral=True)
                await organizations_page(api, click, items, offset + 25)

            view.action("Следующие организации", more)
    await show(
        ctx,
        embed(
            "Мои организации",
            "Доступные действия зависят от роли и прав."
            if items
            else "Организаций пока нет. Создать: /create_organization.",
        ),
        view,
    )


async def organization_page(
    api: HorizonApi, ctx: discord.Interaction, organization_id: str
) -> None:
    data = await api.get("/organizations/" + organization_id, ctx.user.id)
    roles = {
        "owner": "Владелец",
        "administrator": "Администратор",
        "member": "Участник",
    }
    card, view = (
        embed(
            escape(data["name"]),
            "Ваша роль: " + roles.get(data["current_role"], "Участник"),
        ),
        PrivateView(api, ctx.user.id),
    )
    card.add_field(
        name="Участники",
        value=(
            "\n".join(
                escape(item["display_name"])
                + " · "
                + roles.get(item["role"], "Участник")
                for item in data["members"][:12]
            )
            or "Нет участников"
        )[:1024],
        inline=False,
    )
    card.add_field(
        name="Управление",
        value="Участники и отдельные права — в разделе «Организации» на сайте. Все доступные счета, переводы и история — также в /accounts.",
        inline=False,
    )
    if data["accounts"]:

        async def choose(click: discord.Interaction, value: str) -> None:
            await click.response.defer(ephemeral=True)
            account = await api.get("/accounts/" + value + "/balance", click.user.id)
            await show(
                click,
                account_embed(account),
                AccountActions(api, click.user.id, account),
            )

        view.add_item(
            Choice(
                "Счета организации",
                [
                    discord.SelectOption(
                        label=item["name"][:100],
                        value=item["id"],
                        description=money(item["available"]["amount"]),
                    )
                    for item in data["accounts"][:25]
                ],
                choose,
            )
        )
    if data["current_role"] in ("owner", "administrator"):

        async def create(click: discord.Interaction) -> None:
            key = str(uuid.uuid4())

            async def submit(formctx: discord.Interaction, fields: list[str]) -> None:
                await formctx.response.defer(ephemeral=True)
                account = await api.post(
                    "/organizations/" + organization_id + "/accounts",
                    formctx.user.id,
                    {"name": fields[0]},
                    key,
                )
                await show(
                    formctx,
                    account_embed(account),
                    AccountActions(api, formctx.user.id, account),
                )

            await click.response.send_modal(
                Form(
                    "Новый счёт организации",
                    click.user.id,
                    [("Название счёта", 80, True)],
                    submit,
                )
            )

        view.action("Открыть счёт организации", create)
    await show(ctx, card, view)
