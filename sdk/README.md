# Python integration SDK

This typed client calls Minecraft Banking System's existing REST API. It never owns balances or accesses a database directly.

Install with `pip install ./sdk`. Set `HORIZON_API_URL` and `HORIZON_API_KEY` outside source control, then inspect `examples/transfer.py` before running it against a disposable test environment.

The SDK never automatically retries a financial write. Persist the idempotency key at the integration boundary and reuse it only for the identical request after an uncertain network result. The key's scopes, account allowlist, owner's current state, and core permissions all apply.
