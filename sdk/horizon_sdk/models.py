from pydantic import BaseModel


class Money(BaseModel):
    amount: str
    currency: str


class AccountBalance(BaseModel):
    id: str
    status: str
    account_type: str
    balance: Money
    held: Money
    available: Money


class Operation(BaseModel):
    id: str
    type: str
    status: str
    source_account_id: str
    destination_account_id: str
    amount: Money
    created_at: str
    completed_at: str
