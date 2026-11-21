from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from .schemas import PositiveAmount, StrictModel


class AccountSettings(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    is_primary: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Invalid name")
        return value.strip() if value else value


class UserSettings(StrictModel):
    bank_username: Annotated[str, Field(min_length=3, max_length=24)] | None = None
    display_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    language: Literal["ru", "en", "uk-UA"] | None = None
    discord_enabled: bool | None = None
    security_enabled: bool | None = None
    transfers_enabled: bool | None = None
    incoming_enabled: bool | None = None
    outgoing_enabled: bool | None = None
    organizations_enabled: bool | None = None


class RecipientReference(StrictModel):
    kind: Literal["user", "bank_name", "minecraft", "discord", "account"]
    value: Annotated[str, Field(min_length=1, max_length=80)]


class TransferQuoteRequest(StrictModel):
    internal: bool = Field(default=False, strict=True)
    recipient: RecipientReference
    source_account_id: UUID | None = None
    amount: PositiveAmount
    reason: Annotated[str, Field(max_length=500)] = ""


class SavedRecipientRequest(StrictModel):
    recipient: RecipientReference
    label: Annotated[str, Field(max_length=80)] = ""


OrgPermission = Literal[
    "exchange.trade", "exchange.issuer.manage", "exchange.dividends.manage",
    "accounts.read",
    "balance.read",
    "history.read",
    "transfer",
    "payment.create",
    "hold.create",
    "payment_requests.create",
    "payment_requests.read",
    "payment_requests.cancel",
    "analytics.read",
    "analytics.export",
    "analytics.categorize",
    "payroll.read",
    "payroll.employees.manage",
    "payroll.salaries.manage",
    "payroll.batches.create",
    "payroll.approve",
    "payroll.execute",
    "payroll.automatic.manage",
]


def member_defaults() -> list[OrgPermission]:
    return ["accounts.read", "balance.read", "history.read"]


class OrganizationMemberSettings(StrictModel):
    user_id: UUID
    role: Literal["member", "administrator"] = "member"
    permissions: list[OrgPermission] = Field(default_factory=member_defaults)


class OrganizationAccountCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=80)] = "Счёт организации"


class ReadNotifications(StrictModel):
    ids: list[UUID] = Field(default_factory=list, max_length=100)
    all: bool = False
