"""One bounded maintenance run, invoked by the verified-backup UTC systemd timer."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import get_settings
from .grpc.client import GrpcBankingClient
from .logging import configure_logging
from .privacy import cleanup_auth, process_requests

log = structlog.get_logger()


def verify_backup(manifest: Path, root: Path, now: datetime | None = None) -> None:
    root = root.resolve()
    manifest = manifest.resolve()
    if manifest.parent != root:
        raise ValueError("backup manifest must be inside the configured backup directory")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    backup = (root / record["backup_file"]).resolve()
    if backup.parent != root or not backup.name.startswith("horizon-privacy-") or backup.suffix != ".dump":
        raise ValueError("invalid verified backup path")
    stamp = datetime.fromisoformat(record["verified_at"].replace("Z", "+00:00"))
    now = now or datetime.now(UTC)
    if stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(hours=24):
        raise ValueError("a backup restored and checked within 24 hours is required")
    if record.get("restore_checked") is not True or record.get("reconciliation_zero") is not True:
        raise ValueError("successful isolated restore and reconciliation are required")
    with backup.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != record.get("sha256"):
        raise ValueError("verified backup checksum mismatch")


async def tick(session: Any, core: GrpcBankingClient, dry_run: bool = False) -> dict[str, Any]:
    # One maintenance worker across scheduler retries and concurrent hosts.
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(28102810)")):
        return {"busy": 1}
    if dry_run:
        return {
            "pending_erasure_requests": await session.scalar(
                text(
                    "SELECT count(*) FROM auth.privacy_requests WHERE kind='erasure' AND status IN ('approved','blocked','processing')"
                )
            ),
            "old_closed_tickets": await session.scalar(
                text(
                    "SELECT count(*) FROM auth.support_tickets WHERE status='closed' AND closed_at<now()-interval '30 days'"
                )
            ),
            "old_audit_rows": await session.scalar(
                text("SELECT count(*) FROM auth.audit_events WHERE created_at<now()-interval '365 days'")
            ),
        }
    result: dict[str, Any] = {}
    # Disputes must be scoped before financial changes; never guess from free text.
    unreviewed = await session.scalar(text("SELECT EXISTS(SELECT 1 FROM auth.investigations WHERE status='open')"))
    if not unreviewed:
        result["requests"] = await process_requests(session, core)
        basis = os.environ.get("PRIVACY_FINANCIAL_RETENTION_BASIS", "").strip()
        if basis:
            result["finance"] = await core.privacy("minimize", {"basis": basis})
        else:
            result["financial_basis_required"] = 1
    else:
        result["dispute_scope_review_required"] = 1
    result["auth"] = await cleanup_auth(session)
    return result


async def run(dry_run: bool = False) -> None:
    if not dry_run:
        verify_backup(
            Path(os.environ.get("PRIVACY_BACKUP_MANIFEST", "/privacy-backups/verified.json")),
            Path(os.environ.get("PRIVACY_BACKUP_ROOT", "/privacy-backups")),
        )
    engine = create_async_engine(get_settings().auth_database_url)
    core = GrpcBankingClient(get_settings())
    try:
        async with async_sessionmaker(engine)() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL timezone='UTC'"))
                result = await tick(session, core, dry_run)
            # Counts and fixed status flags only: never subjects, reasons or error strings.
            log.info("privacy_maintenance", counts=result, dry_run=dry_run)
    finally:
        await core.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    configure_logging()
    try:
        asyncio.run(run(args.dry_run))
    except Exception:
        log.error("privacy_maintenance_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
