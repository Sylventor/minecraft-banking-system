from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

Amount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,15})\.[0-9]{2}$")]


def positive_amount(value: str) -> str:
    if Decimal(value) <= 0:
        raise ValueError("amount must be positive")
    return value


PositiveAmount = Annotated[Amount, AfterValidator(positive_amount)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IdempotencyKey = Annotated[str, Field(min_length=16, max_length=255, pattern=r"^[A-Za-z0-9._:-]+$")]


class ErrorResponse(BaseModel):
    error: str
    code: str
    request_id: str


class Money(BaseModel):
    amount: Amount
    currency: Literal["DB"] = "DB"

    @field_validator("amount")
    @classmethod
    def amount_is_supported(cls, value: str) -> str:
        try:
            if Decimal(value) > Decimal("9999999999999999.99"):
                raise ValueError
        except (InvalidOperation, ValueError) as error:
            raise ValueError("amount is outside supported range") from error
        return value


class TransferRequest(StrictModel):
    source_account_id: UUID
    destination_account_id: UUID
    amount: Amount
    reason: Annotated[str, Field(max_length=500)] = ""

    @field_validator("amount")
    @classmethod
    def positive(cls, value: str) -> str:
        if Decimal(value) <= 0:
            raise ValueError("amount must be positive")
        return value


class AccountCreateRequest(StrictModel):
    account_type: Literal["personal"] = "personal"
    name: Annotated[str, Field(min_length=1, max_length=80)] = "Личный счёт"


class HoldRequest(StrictModel):
    account_id: UUID
    amount: PositiveAmount
    reason: Annotated[str, Field(max_length=500)] = ""


class HoldActionRequest(StrictModel):
    destination_account_id: UUID | None = None
    reason: Annotated[str, Field(max_length=500)] = ""


class RefundRequest(StrictModel):
    source_account_id: UUID
    original_operation_id: UUID
    amount: PositiveAmount
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class IssueRequest(StrictModel):
    target_account_id: UUID
    amount: PositiveAmount
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    authority_reference: Annotated[str, Field(min_length=3, max_length=120)]


class ApiKeyCreateRequest(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    scopes: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9._:-]{1,63}$")]] = Field(min_length=1, max_length=20)
    account_ids: list[UUID] = Field(default_factory=list, max_length=100)
    organization_id: UUID | None = None


class ApiKeyCreated(BaseModel):
    id: UUID
    prefix: str
    secret: str
    name: str
    scopes: list[str]
    created_at: datetime


class ApiKeyView(BaseModel):
    id: UUID
    prefix: str
    name: str
    scopes: list[str]
    created_at: datetime
    revoked_at: datetime | None
    account_ids: list[UUID] = Field(default_factory=list)
    organization_id: UUID | None = None


class OrganizationCreate(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=120)]


class AccountView(BaseModel):
    id: UUID
    status: Literal["active", "frozen", "closed"]
    account_type: str
    balance: Money
    held: Money
    available: Money
    public_number: str = ""
    name: str = "Личный счёт"
    is_primary: bool = False
    owner_id: str = ""
    organization_id: str = ""
    permissions: list[str] = Field(default_factory=list)


class OperationView(BaseModel):
    id: UUID
    type: str
    status: str
    source_account_id: str
    destination_account_id: str
    amount: Money
    created_at: str
    completed_at: str
    related_operation_id: str = ""
    hold_id: str = ""
    reason: str = ""
    fee: Money = Field(default_factory=lambda: Money(amount="0.00"))
    source_public_number: str = ""
    destination_public_number: str = ""
    source_owner_id: str = ""
    destination_owner_id: str = ""
    source_name: str = ""
    destination_name: str = ""
    source_organization_id: str = ""
    destination_organization_id: str = ""


class OperationPage(BaseModel):
    items: list[OperationView]
    next_cursor: str | None


class AccountsPage(BaseModel):
    items: list[AccountView]
    next_cursor: str | None
