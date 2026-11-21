# Financial model

`DB` is fictional in-game currency, not a real-money denomination. Amounts are JSON/protobuf strings with exactly two fractional digits. C++ parses them into checked signed 64-bit cents; PostgreSQL stores exact `numeric` values. Financial calculations do not use binary floating point.

For ordinary accounts, `balance >= held >= 0`, and available funds equal `balance - held`. Account rows are locked before movement. A transfer debits one account and credits another within one PostgreSQL transaction, records paired ledger entries, and marks its idempotency record completed in that transaction.

The common maximum is `9999999999999999.99 DB`. Financial mutations require positive amounts. Extra decimal places and arithmetic overflow are rejected rather than rounded. Display formatting does not change stored values. Current fees are zero.

## Holds and refunds

Holds reserve existing available funds. Release removes the reservation. Capture debits the reserved source and credits a destination. Refunds lock the immutable original operation before aggregating prior refunds, require refund authority and source permission, and cannot exceed the original remaining amount.

## Issuance and physical settlement

Issuance is an explicit operation from the configured system account. It requires financial authority, a receiving-account grant, and an authority reference. It is not a direct balance update. Ordinary users cannot debit the system account.

ATM deposits and withdrawals use a separate leased state machine. The coordinator persists physical intent, compares both inventory deltas, and reconciles uncertain outcomes. A lost financial response does not trigger automatic reversal or another physical delivery. Unknown physical outcomes require review.

HPay snapshots a product proposal before confirmation and settles through the same ledger. Exchange and payroll settlement also reuse the core's financial path. Their business state and corresponding financial result are committed together where designed.

## Database integrity and reconciliation

Deferred constraints check paired ledger entries, balance/ledger agreement, and holds. Financial operations and audit evidence are append-only. The database-role boundary is tested independently of HTTP permissions.

```sql
SELECT * FROM finance.reconciliation
WHERE balance <> ledger_balance OR held <> active_holds;
```

The expected result is empty. Preserve evidence and isolate affected writers before investigating divergence. Never fix a mismatch by directly editing a live balance.

These mechanisms reduce specific failure modes. They do not constitute a proof that every feature is correct or a guarantee of exactly-once effects in an external Minecraft server.
