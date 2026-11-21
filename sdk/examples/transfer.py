import os
from horizon_sdk import HorizonClient

with HorizonClient(
    os.environ["HORIZON_API_URL"], os.environ["HORIZON_API_KEY"]
) as bank:
    # Save this key with the business event before retrying after an uncertain outcome.
    result = bank.transfer(
        os.environ["SOURCE_ACCOUNT_ID"],
        os.environ["DESTINATION_ACCOUNT_ID"],
        "25.00",
        reason="Game reward",
        idempotency_key=os.environ["HORIZON_IDEMPOTENCY_KEY"],
    )
    print(result.id)
