# Operational boundaries

## Backups and restoration

The example backup script uses PostgreSQL client tools and operator-provided service/credential files. Choose a private backup location and an explicit retention policy. Test restoration into a new isolated database before trusting a backup. A successful dump command alone is not a restoration test.

Existing privacy-maintenance scripts require a verified backup manifest and operator configuration. Do not enable systemd timers merely to explore the portfolio. Review deletion/retention controls, outstanding investigations, unresolved ATM operations, and the chosen financial retention basis first.

There is no legal-compliance certification or universal retention period supplied by this repository. The example `.env` uses a seven-day backup retention value for explicit local/operator review; it is not a reason to delete another deployment's backups.

## Financial reconciliation

Use the read-only reconciliation query in [Financial model](financial-model.md). Preserve audit evidence when investigating mismatches. Do not reset balances or remove rows to make a report appear clean.

ATM operations with unknown physical outcomes retain a fence. Inspect the actual game container and saved evidence, then use the authorized review workflow. Restarting a bot must not repeat an uncertain delivery.

## Diagnostics

```bash
docker compose ps -a
docker compose logs --since=10m --tail=100 backend core web
docker compose exec -T web wget -S -O - http://backend:8000/readyz
```

Do not publish unsanitized logs. HTTP logs intentionally record request IDs and status metadata rather than query strings, cookies, authorization headers, or OAuth codes. ATM debug output is opt-in and redacts recognized secrets/private-message data.

## Privacy capabilities

The source includes support-assisted rights requests, paginated exports, processing restrictions, legal holds, and guarded maintenance. Structural financial evidence and unresolved physical operations may prevent immediate erasure. See `backend/app/privacy.py`, `backend/app/privacy_worker.py`, and the corresponding core implementation for the actual controls.

The portfolio's legal-page text is a demonstration template with synthetic contact details. An operator must replace it with an accurate policy and independently determine their obligations before collecting real users' data.
