from __future__ import annotations

import os
import asyncio
from contextlib import suppress
import uuid
from urllib.parse import urlencode

import discord
import httpx
from discord import app_commands

from .api import HorizonApi
from .interactions import (
    AccountActions,
    Form,
    MainMenu,
    account_by_name,
    accounts_page,
    error_message,
    find_recipient,
    history_page,
    open_menu,
    organization_page,
    organizations_page,
    profile_page,
    show,
    transfer_form,
)
from .presentation import account_embed, amount_input, embed, escape, receipt_embed
from .notifications import NotificationWorker

API_URL = os.environ.get("HORIZON_API_URL", "http://backend:8000/api/v1")
PUBLIC_ORIGIN = os.environ.get("PUBLIC_ORIGIN", "http://localhost:8080").rstrip("/")


class RussianCommands(app_commands.Translator):
    names = {
        "start": "начать",
        "menu": "меню",
        "notifications": "уведомления",
        "balance": "баланс",
        "accounts": "счета",
        "transfer": "перевод",
        "history": "история",
        "profile": "профиль",
        "organizations": "организации",
        "help": "помощь",
        "operation": "операция",
        "open_account": "открыть_счёт",
        "create_organization": "создать_организацию",
        "minecraft": "майнкрафт",
        "link_minecraft": "привязать_майнкрафт",
    }

    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        if (
            locale == discord.Locale.russian
            and context.location == app_commands.TranslationContextLocation.command_name
        ):
            return self.names.get(string.message)
        return None


class HorizonBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(
            intents=discord.Intents.none(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.tree = app_commands.CommandTree(self)
        self.api = HorizonApi(API_URL, os.environ.get("DISCORD_BOT_INTERNAL_TOKEN", ""))
        self.notification_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        await self.tree.set_translator(RussianCommands())
        await self.tree.sync()
        self.notification_task = asyncio.create_task(
            NotificationWorker(self, self.api).run()
        )

    async def close(self) -> None:
        if self.notification_task:
            self.notification_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.notification_task
        await self.api.close()
        await super().close()


bot = HorizonBot()


@bot.tree.command(
    name="notifications",
    description="Настройки Discord-уведомлений и состояние доставки",
)
async def notifications(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    settings = await bot.api.get("/me/notification-settings", interaction.user.id)
    status = await bot.api.get(
        "/me/notification-deliveries?limit=3", interaction.user.id
    )
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="Настроить уведомления", url=PUBLIC_ORIGIN + "/#/settings"
        )
    )
    await show(
        interaction,
        embed(
            "Уведомления Banking System",
            (
                "Discord-уведомления включены."
                if settings["discord_enabled"]
                else "Обычные Discord-уведомления отключены."
            )
            + "\n"
            + (
                "Сервис доставки работает."
                if status["worker_online"]
                else "Сервис доставки ожидает подключения."
            )
            + "\nКритические события безопасности отправляются независимо от настроек.",
        ),
        view,
    )


@bot.tree.error
async def command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    await error_message(interaction, getattr(error, "original", error))


@bot.tree.command(name="menu", description="Открыть главное меню Banking System")
async def menu(interaction: discord.Interaction) -> None:
    await open_menu(bot.api, interaction)


@bot.tree.command(name="start", description="Войти в банк или открыть свой кабинет")
async def start(interaction: discord.Interaction) -> None:
    try:
        await open_menu(bot.api, interaction)
    except httpx.HTTPStatusError as error:
        if error.response.status_code not in (401, 403, 404):
            raise
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Войти через Discord",
                url=PUBLIC_ORIGIN + "/api/v1/auth/discord/login",
            )
        )
        await show(
            interaction,
            embed(
                "Добро пожаловать в Banking System",
                "Войдите через Discord на сайте. При первом входе основной личный счёт создаётся автоматически. Если профиль заблокирован, обратитесь к администрации.",
            ),
            view,
        )


@bot.tree.command(
    name="balance", description="Баланс основного счёта или другого выбранного счёта"
)
@app_commands.describe(account="Необязательно: название или публичные реквизиты HB-")
async def balance(interaction: discord.Interaction, account: str = "") -> None:
    await interaction.response.defer(ephemeral=True)
    data = await account_by_name(bot.api, interaction.user.id, account)
    await show(
        interaction,
        account_embed(data),
        AccountActions(bot.api, interaction.user.id, data),
    )


@bot.tree.command(
    name="accounts",
    description="Выбрать счёт, посмотреть реквизиты и доступные действия",
)
async def accounts(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    await accounts_page(bot.api, interaction)


@bot.tree.command(name="open_account", description="Открыть дополнительный личный счёт")
async def open_account(interaction: discord.Interaction) -> None:
    key = str(uuid.uuid4())

    async def submit(ctx: discord.Interaction, fields: list[str]) -> None:
        await ctx.response.defer(ephemeral=True)
        data = await bot.api.post(
            "/accounts",
            ctx.user.id,
            {"account_type": "personal", "name": fields[0]},
            key,
        )
        await show(ctx, account_embed(data), AccountActions(bot.api, ctx.user.id, data))

    await interaction.response.send_modal(
        Form(
            "Открыть счёт",
            interaction.user.id,
            [("Название нового счёта", 80, True)],
            submit,
        )
    )


@bot.tree.command(
    name="transfer", description="Найти получателя по нику и подтвердить перевод"
)
@app_commands.describe(
    recipient="Банковское имя, Minecraft, Discord или реквизиты HB-",
    amount="Сумма, например 125,50",
    reason="Комментарий к переводу",
)
async def transfer(
    interaction: discord.Interaction,
    recipient: str = "",
    amount: str = "",
    reason: str = "",
) -> None:
    if not recipient or not amount:
        await interaction.response.send_modal(
            transfer_form(bot.api, interaction.user.id)
        )
        return
    await interaction.response.defer(ephemeral=True)
    await find_recipient(bot.api, interaction, recipient, amount_input(amount), reason)


@transfer.autocomplete("recipient")
async def recipient_choices(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if len(current.strip()) < 3:
        return []
    try:
        result = await bot.api.get(
            "/recipients/search?" + urlencode({"q": current}), interaction.user.id
        )
        items = result["items"]
        return [
            app_commands.Choice(
                name=(
                    item["display_name"]
                    + " · "
                    + (
                        "@" + item["bank_username"]
                        if item.get("bank_username")
                        else item["public_number"]
                    )
                )[:100],
                value="user:" + item["id"]
                if item.get("id") and not current.upper().startswith("HB-")
                else item["public_number"],
            )
            for item in items[:25]
        ]
    except (httpx.HTTPError, ValueError):
        return []


@bot.tree.command(
    name="history", description="История основного или выбранного счёта с квитанциями"
)
@app_commands.describe(account="Необязательно: название или реквизиты HB-")
async def history(interaction: discord.Interaction, account: str = "") -> None:
    await interaction.response.defer(ephemeral=True)
    data = await account_by_name(bot.api, interaction.user.id, account)
    await history_page(bot.api, interaction, data["id"])


@bot.tree.command(
    name="operation",
    description="Выбрать операцию из истории или открыть квитанцию по номеру",
)
async def operation(interaction: discord.Interaction, operation_id: str = "") -> None:
    await interaction.response.defer(ephemeral=True)
    if operation_id:
        data = await bot.api.get(
            "/receipts/" + str(uuid.UUID(operation_id)), interaction.user.id
        )
        await show(interaction, receipt_embed(data))
    else:
        data = await bot.api.get("/accounts/primary", interaction.user.id)
        await history_page(bot.api, interaction, data["id"])


@bot.tree.command(
    name="profile", description="Мой банковский профиль и привязанные аккаунты"
)
async def profile(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    await profile_page(bot.api, interaction)


@bot.tree.command(name="organizations", description="Мои организации, роли и счета")
async def organizations(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    await organizations_page(bot.api, interaction)


@bot.tree.command(
    name="create_organization", description="Создать организацию и управлять её счетами"
)
@app_commands.describe(name="Название организации")
async def create_organization(interaction: discord.Interaction, name: str) -> None:
    await interaction.response.defer(ephemeral=True)
    data = await bot.api.post("/organizations", interaction.user.id, {"name": name})
    await organization_page(bot.api, interaction, data["id"])


@bot.tree.command(
    name="minecraft", description="Проверить подтверждённую привязку Minecraft"
)
async def minecraft(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    data = await bot.api.get("/minecraft", interaction.user.id)
    card = embed(
        "Minecraft",
        "Привязка подтверждена доверенным сервером: **"
        + escape(data["link"]["nickname"])
        + "**"
        if data["link"]
        else "Подтверждённой привязки пока нет.",
    )
    card.add_field(
        name="Подтверждение",
        value="Получите одноразовый код: /link_minecraft."
        if data["integration_enabled"]
        else "Недоступно: администрация ещё не настроила доверенный verifier. Введённый ник не подтверждает владение аккаунтом.",
        inline=False,
    )
    await show(interaction, card)


@bot.tree.command(
    name="link_minecraft",
    description="Получить одноразовый код для доверенного Minecraft-сервера",
)
async def link_minecraft(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    data = await bot.api.post("/minecraft/link-code", interaction.user.id, {})
    await show(
        interaction,
        embed(
            "Подтвердите Minecraft на сервере",
            "Передайте эту строку **только доверенному серверу банка** по инструкции администратора. Не отправляйте её другим игрокам.\n\n`"
            + data["challenge_id"]
            + ":"
            + data["code"]
            + "`\n\nДействует до "
            + data["expires_at"]
            + ". До ответа сервера привязка не подтверждена.",
        ),
    )


@bot.tree.command(
    name="help",
    description="Как пользоваться Banking System без технических идентификаторов",
)
async def help_command(interaction: discord.Interaction) -> None:
    await show(
        interaction,
        embed(
            "Помощь Banking System",
            "/start — вход и регистрация\n/menu — банковское меню\n/balance — баланс основного счёта\n/transfer — перевод по нику с подтверждением\n/accounts — счета и реквизиты\n/history · /operation — история и квитанции\n/profile — профиль\n/organizations · /create_organization — организации\n/open_account — дополнительный счёт\n/minecraft · /link_minecraft — подтверждённая привязка\n\nВсе финансовые данные видны только вам. После ошибки перевода повторяйте то же подтверждение, не создавайте новое.",
        ),
        MainMenu(bot.api, interaction.user.id),
    )


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token or len(os.environ.get("DISCORD_BOT_INTERNAL_TOKEN", "")) < 32:
        raise SystemExit("Не настроены обязательные секреты Discord-сервиса")
    bot.run(token, log_handler=None)
