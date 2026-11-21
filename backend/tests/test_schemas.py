import pytest
from pydantic import ValidationError

from app.grpc.client import fingerprint
from app.schemas import Money, TransferRequest


def test_money_accepts_exact_decimal_string() -> None:
    assert Money(amount="125.40").amount == "125.40"


@pytest.mark.parametrize("amount", ["1", "1.0", "01.00", "1.000", "-1.00"])
def test_money_rejects_ambiguous_amount(amount: str) -> None:
    with pytest.raises(ValidationError):
        Money(amount=amount)


def test_transfer_fingerprint_is_stable_and_binds_actor() -> None:
    payload = TransferRequest(
        source_account_id="00000000-0000-0000-0000-000000000010",
        destination_account_id="00000000-0000-0000-0000-000000000011",
        amount="1.00",
    ).model_dump(mode="json")
    assert fingerprint("Transfer", "00000000-0000-0000-0000-000000000001", payload) == fingerprint(
        "Transfer", "00000000-0000-0000-0000-000000000001", payload
    )
    assert fingerprint("Transfer", "00000000-0000-0000-0000-000000000001", payload) != fingerprint(
        "Transfer", "00000000-0000-0000-0000-000000000002", payload
    )
