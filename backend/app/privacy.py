"""Verified privacy workflow; no secrets in exports and no financial SQL from auth."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .experience import audit, rows
from .grpc.client import GrpcBankingClient
from .rbac import protect_last_superadmin

SECRET_COLUMNS = {
    "secret",
    "code",
    "key",
    "api_key",
    "access_token",
    "refresh_token",
    "password_hash",
    "token_hash",
    "state_hash",
    "code_hash",
    "secret_hash",
    "session_hash",
    "lease_token",
    "fingerprint",
    "request_fingerprint",
    "idempotency_key",
    "creation_key",
    "pay_key",
}


def without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: without_secrets(v)
            for k, v in value.items()
            if k not in SECRET_COLUMNS
            and not any(word in k.lower() for word in ("password", "authorization", "cookie"))
            and not k.lower().endswith(("_token", "_hash"))
        }
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value


async def auth_inventory(session: AsyncSession) -> list[str]:
    return list(
        (
            await session.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='auth' "
                    "AND tablename<>'alembic_version' ORDER BY tablename"
                )
            )
        ).all()
    )


async def export_page(session: AsyncSession, subject: uuid.UUID, table: str, offset: int) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z_][a-z_0-9]*", table) or table not in await auth_inventory(session):
        raise HTTPException(422, "Invalid privacy export table")
    if not 0 <= offset <= 10_000_000:
        raise HTTPException(422, "Invalid privacy export page")
    scopes = [str(subject)]
    scopes += [
        str(v)
        for v in (
            await session.scalars(text("SELECT id FROM auth.support_tickets WHERE user_id=:u"), {"u": subject})
        ).all()
    ]
    scopes += [
        str(v)
        for v in (
            await session.scalars(text("SELECT id FROM auth.organizations WHERE owner_id=:u"), {"u": subject})
        ).all()
    ]
    data = await rows(
        session,
        f'SELECT to_jsonb(t) AS row FROM auth."{table}" t '
        "WHERE to_jsonb(t)::text LIKE ANY(CAST(:patterns AS text[])) ORDER BY to_jsonb(t)::text OFFSET :off LIMIT 100",
        {"patterns": ["%" + v + "%" for v in scopes], "off": offset},
    )
    return {
        "rows": [without_secrets(item["row"]) for item in data],
        "next_offset": offset + 100 if len(data) == 100 else None,
        "classification": "staff_review_only: review and remove third-party/private staff information before disclosure",
    }


async def blockers(session: AsyncSession, subject: uuid.UUID, case_id: uuid.UUID | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    hold = await session.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM auth.privacy_holds WHERE user_id=:u "
            "AND released_at IS NULL AND expires_at>now())"
        ),
        {"u": subject},
    )
    if hold:
        result["legal_hold"] = True
    # Investigations can contain arbitrary subject types/notes. Never infer that an
    # unclassified open dispute is unrelated; staff must classify/resolve it first.
    if await session.scalar(text("SELECT EXISTS(SELECT 1 FROM auth.investigations WHERE status='open')")):
        result["open_investigation_requires_scope_review"] = True
    pending = await session.scalar(
        text(
            "SELECT count(*) FROM auth.privacy_requests WHERE user_id=:u "
            "AND kind IN ('access','portability','rectification') AND status NOT IN ('completed','rejected') AND id<>:case"
        ),
        {"u": subject, "case": case_id or uuid.UUID(int=0)},
    )
    if pending:
        result["other_rights_requests"] = int(pending)
    return result


async def revoke_access(session: AsyncSession, subject: uuid.UUID) -> None:
    await session.execute(
        text(
            "UPDATE auth.application_users SET status='blocked',processing_restricted_at=COALESCE(processing_restricted_at,now()) WHERE id=:u"
        ),
        {"u": subject},
    )
    await session.execute(
        text("UPDATE auth.user_sessions SET revoked_at=COALESCE(revoked_at,now()) WHERE user_id=:u"), {"u": subject}
    )
    await session.execute(
        text("UPDATE auth.api_keys SET revoked_at=COALESCE(revoked_at,now()) WHERE owner_id=:u"), {"u": subject}
    )
    await session.execute(
        text(
            "UPDATE auth.discord_outbox SET status='suppressed',lease_token=NULL,lease_until=NULL,payload='{}' WHERE user_id=:u AND status IN ('pending','sending')"
        ),
        {"u": subject},
    )


async def erase_auth(session: AsyncSession, subject: uuid.UUID) -> dict[str, Any]:
    """Profile is a non-restorable FK tombstone, not claimed anonymous financial data."""
    await protect_last_superadmin(session, subject)
    await session.execute(text("SELECT id FROM auth.application_users WHERE id=:u FOR UPDATE"), {"u": subject})
    await revoke_access(session, subject)
    await session.execute(
        text(
            "UPDATE auth.application_users SET status='archived',erased_at=COALESCE(erased_at,now()),"
            "display_name='',bank_username=NULL,age_confirmed_at=NULL,banking_ready=false,language='ru' WHERE id=:u"
        ),
        {"u": subject},
    )
    # Remove both current identifiers and copies in historical/recovery tables.
    for table, column in [
        ("user_sessions", "user_id"),
        ("password_credentials", "user_id"),
        ("oauth_states", "user_id"),
        ("api_keys", "owner_id"),
        ("external_identities", "user_id"),
        ("minecraft_verified_links", "user_id"),
        ("minecraft_links", "user_id"),
        ("minecraft_link_challenges", "user_id"),
        ("minecraft_atm_challenges", "user_id"),
        ("minecraft_nickname_history", "user_id"),
        ("bank_username_history", "user_id"),
        ("discord_outbox", "user_id"),
        ("user_notifications", "user_id"),
        ("notification_preferences", "user_id"),
        ("organization_members", "user_id"),
        ("user_roles", "user_id"),
        ("user_permissions", "user_id"),
        ("terms_acceptances", "user_id"),
        ("recipient_lookup_windows", "user_id"),
    ]:
        await session.execute(text(f'DELETE FROM auth."{table}" WHERE "{column}"=:u'), {"u": subject})
    for table in ["saved_recipients", "recent_recipients"]:
        await session.execute(
            text(f'DELETE FROM auth."{table}" WHERE user_id=:u OR recipient_user_id=:u'), {"u": subject}
        )
    await session.execute(
        text("DELETE FROM auth.transfer_quotes WHERE user_id=:u OR recipient_user_id=:u"), {"u": subject}
    )
    owned = "SELECT id FROM auth.support_tickets WHERE user_id=:u"
    await session.execute(
        text(f"UPDATE auth.privacy_requests SET ticket_id=NULL WHERE ticket_id IN ({owned})"), {"u": subject}
    )
    await session.execute(
        text(f"UPDATE auth.privacy_holds SET ticket_id=NULL WHERE ticket_id IN ({owned})"), {"u": subject}
    )
    for table in ["support_reads", "support_internal_notes", "support_messages"]:
        await session.execute(text(f"DELETE FROM auth.{table} WHERE ticket_id IN ({owned})"), {"u": subject})
    await session.execute(text("DELETE FROM auth.support_tickets WHERE user_id=:u"), {"u": subject})
    await session.execute(text("DELETE FROM auth.support_reads WHERE user_id=:u"), {"u": subject})
    for table in ["support_messages", "support_internal_notes"]:
        await session.execute(text(f"UPDATE auth.{table} SET body='[redacted]' WHERE author_id=:u"), {"u": subject})
    await session.execute(
        text("UPDATE auth.audit_events SET metadata_json='{}' WHERE actor_id=:u OR target_id=CAST(:u AS text)"),
        {"u": subject},
    )
    await session.execute(
        text("UPDATE auth.investigation_notes SET body='[redacted]' WHERE author_id=:u"), {"u": subject}
    )
    # Actor/author UUIDs needed for protected history remain pseudonymous; free-text
    # copies are reviewed and scrubbed before an erasure case can be approved.
    return {
        "profile": "erased",
        "remaining": "minimal pseudonymous FK/ledger/audit references under recorded retention basis",
        "notification": "manual_required",
    }


async def cleanup_auth(session: AsyncSession) -> dict[str, int]:
    """Bounded UTC maintenance. Caller must establish backup/restore authorization."""
    counts: dict[str, int] = {}
    if await session.scalar(text("SELECT EXISTS(SELECT 1 FROM auth.investigations WHERE status='open')")):
        return {"blocked_open_investigations": 1}
    for table, condition in [
        ("authentication_limits", "window_start<now()-interval '30 days'"),
        ("recipient_lookup_windows", "window_start<now()-interval '30 days'"),
        ("oauth_states", "expires_at<now()-interval '30 days'"),
        (
            "user_sessions",
            "LEAST(expires_at,absolute_expires_at,COALESCE(revoked_at,expires_at))<now()-interval '30 days'",
        ),
        ("minecraft_link_challenges", "expires_at<now()-interval '30 days'"),
        ("minecraft_links", "verified_at IS NULL AND expires_at<now()-interval '30 days'"),
        ("minecraft_atm_challenges", "expires_at<now()-interval '30 days' AND status NOT IN ('pending','delivered')"),
        ("transfer_quotes", "expires_at<now()-interval '30 days'"),
    ]:
        # Holds suspend subject-scoped deletion. Hashed IP buckets have no subject identity.
        scope = "" if table == "authentication_limits" else " AND NOT auth.privacy_has_hold(user_id)"
        result = await session.execute(
            text(
                f"DELETE FROM auth.{table} WHERE ctid IN (SELECT ctid FROM auth.{table} WHERE {condition}{scope} LIMIT 1000)"
            )
        )
        counts[table] = int(getattr(result, "rowcount", 0))
    eligible = (
        "SELECT id FROM auth.user_notifications WHERE NOT auth.privacy_has_hold(user_id) AND "
        "((read_at IS NOT NULL AND read_at<now()-interval '30 days') OR (read_at IS NULL AND created_at<now()-interval '90 days')) LIMIT 1000"
    )
    await session.execute(
        text(f"DELETE FROM auth.discord_outbox WHERE notification_id IN ({eligible}) AND status<>'sending'")
    )
    result = await session.execute(
        text(
            f"DELETE FROM auth.user_notifications WHERE id IN ({eligible}) AND NOT EXISTS(SELECT 1 FROM auth.discord_outbox o WHERE o.notification_id=auth.user_notifications.id)"
        )
    )
    counts["notifications"] = int(getattr(result, "rowcount", 0))
    tickets = (
        "SELECT t.id FROM auth.support_tickets t WHERE t.status='closed' AND t.closed_at<now()-interval '30 days' "
        "AND NOT auth.privacy_has_hold(t.user_id) AND NOT EXISTS(SELECT 1 FROM auth.privacy_requests p WHERE p.ticket_id=t.id AND p.status NOT IN ('completed','rejected')) "
        "AND NOT EXISTS(SELECT 1 FROM auth.privacy_holds h WHERE h.ticket_id=t.id AND h.released_at IS NULL AND h.expires_at>now()) LIMIT 100"
    )
    ids = list((await session.scalars(text(tickets))).all())
    if ids:
        for table in ["privacy_requests", "privacy_holds"]:
            await session.execute(
                text(f"UPDATE auth.{table} SET ticket_id=NULL WHERE ticket_id=ANY(CAST(:ids AS uuid[]))"), {"ids": ids}
            )
        for table in ["support_reads", "support_internal_notes", "support_messages"]:
            await session.execute(
                text(f"DELETE FROM auth.{table} WHERE ticket_id=ANY(CAST(:ids AS uuid[]))"), {"ids": ids}
            )
        await session.execute(text("DELETE FROM auth.support_tickets WHERE id=ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
    counts["closed_support_tickets"] = len(ids)
    result = await session.execute(
        text(
            "DELETE FROM auth.audit_events WHERE ctid IN (SELECT e.ctid FROM auth.audit_events e WHERE created_at<now()-interval '365 days' AND NOT auth.privacy_has_hold(actor_id) "
            "AND NOT EXISTS(SELECT 1 FROM auth.privacy_holds h WHERE h.user_id::text=e.target_id AND h.released_at IS NULL AND h.expires_at>now()) "
            "AND NOT EXISTS(SELECT 1 FROM auth.privacy_requests p WHERE p.user_id::text=e.target_id AND p.status NOT IN ('completed','rejected')) LIMIT 1000)"
        )
    )
    counts["audit_events"] = int(getattr(result, "rowcount", 0))
    await session.execute(text("DELETE FROM auth.privacy_device_tasks WHERE acknowledged_at<now()-interval '30 days'"))
    await session.execute(
        text(
            "UPDATE auth.privacy_requests SET reason='',result='{}' WHERE id IN "
            "(SELECT id FROM auth.privacy_requests WHERE status IN ('completed','rejected') AND updated_at<now()-interval '365 days' "
            "AND NOT auth.privacy_has_hold(user_id) AND (reason<>'' OR result<>'{}') LIMIT 1000)"
        )
    )
    return counts


async def process_requests(session: AsyncSession, core: GrpcBankingClient) -> dict[str, int]:
    await session.execute(text("SELECT pg_advisory_xact_lock(220022)"))
    counts = {"completed": 0, "blocked": 0}
    cases = await rows(
        session,
        "SELECT * FROM auth.privacy_requests WHERE status IN ('approved','processing','blocked') "
        "AND kind IN ('erasure','restriction','objection','consent_withdrawal') ORDER BY created_at LIMIT 5 FOR UPDATE SKIP LOCKED",
    )
    for case in cases:
        subject = case["user_id"]
        problem = await blockers(session, subject, case["id"])
        inventory: dict[str, Any] = {}
        if case["kind"] == "erasure":
            try:
                await protect_last_superadmin(session, subject)
            except HTTPException:
                problem["last_active_superadmin"] = True
            inventory = await core.privacy("inventory", {"user_id": str(subject)})
            problem.update(inventory["blockers"])
        if problem:
            await session.execute(
                text(
                    "UPDATE auth.privacy_requests SET status='blocked',result=CAST(:r AS jsonb),updated_at=now() WHERE id=:id"
                ),
                {"id": case["id"], "r": json.dumps({"blockers": problem})},
            )
            counts["blocked"] += 1
            continue
        result = await core.privacy(
            "erase" if case["kind"] == "erasure" else "restrict",
            {"user_id": str(subject), "basis": case["retention_basis"]},
        )
        if result.get("blocked"):
            await session.execute(
                text(
                    "UPDATE auth.privacy_requests SET status='blocked',result=CAST(:r AS jsonb),updated_at=now() WHERE id=:id"
                ),
                {"id": case["id"], "r": json.dumps(result)},
            )
            counts["blocked"] += 1
            continue
        if case["kind"] == "erasure":
            if inventory.get("has_atm_storage"):
                aliases = list(inventory.get("atm_aliases", []))
                aliases += list(
                    (
                        await session.scalars(
                            text("SELECT nickname FROM auth.minecraft_verified_links WHERE user_id=:u"), {"u": subject}
                        )
                    ).all()
                )
                await session.execute(
                    text(
                        "INSERT INTO auth.privacy_device_tasks(case_id,user_id,nicknames) VALUES(:id,:u,:names) ON CONFLICT(case_id) DO NOTHING"
                    ),
                    {"id": case["id"], "u": subject, "names": sorted(set(aliases))},
                )
            result = await erase_auth(session, subject)
            pending = await session.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM auth.privacy_device_tasks WHERE case_id=:id AND acknowledged_at IS NULL)"
                ),
                {"id": case["id"]},
            )
            if pending:
                await session.execute(
                    text(
                        "UPDATE auth.privacy_requests SET status='processing',updated_at=now(),result=CAST(:r AS jsonb) WHERE id=:id"
                    ),
                    {"id": case["id"], "r": json.dumps({**result, "awaiting_atm_journal_erasure": True})},
                )
                continue
        else:
            await revoke_access(session, subject)
        await session.execute(
            text(
                "UPDATE auth.privacy_requests SET status='completed',completed_at=now(),updated_at=now(),"
                "notification_status='manual_required',result=CAST(:r AS jsonb) WHERE id=:id"
            ),
            {"id": case["id"], "r": json.dumps(result)},
        )
        audit(session, str(case["decided_by"]), "privacy.completed", str(case["id"]), {"kind": case["kind"]})
        counts["completed"] += 1
    return counts
