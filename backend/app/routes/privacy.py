"""Staff-only rights requests. Public requests arrive via support or the published email."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from ..database import get_session
from ..dependencies import get_core
from ..experience import audit, rows
from ..grpc.client import GrpcBankingClient
from ..privacy import auth_inventory, blockers, export_page, without_secrets
from ..rbac import administration_lock, protect_last_superadmin
from ..schemas import StrictModel
from ..security import Principal, require_admin

router = APIRouter(prefix="/api/v1/admin/privacy", tags=["privacy administration"])
Kind = Literal["access", "rectification", "erasure", "restriction", "objection", "portability", "consent_withdrawal"]


class Create(StrictModel):
    user_id: uuid.UUID
    kind: Kind
    source: Literal["support", "email"]
    ticket_id: uuid.UUID | None = None


class Action(StrictModel):
    action: Literal["verify", "approve", "reject", "complete", "notified"]
    confirmed: bool = Field(strict=True)
    reason: str = Field(min_length=3, max_length=1000)
    review_confirmed: bool = Field(default=False, strict=True)
    retention_basis: str = Field(default="", max_length=1000)


class Export(StrictModel):
    schema_name: Literal["auth", "finance"]
    table: str = Field(pattern=r"^[a-z_][a-z_0-9]*$")
    offset: int = Field(default=0, ge=0, le=10000000)


class Hold(StrictModel):
    user_id: uuid.UUID
    basis: str = Field(min_length=3, max_length=1000)
    expires_at: datetime
    ticket_id: uuid.UUID | None = None


async def case(session: AsyncSession, ident: uuid.UUID, lock: bool = False) -> dict[str, Any]:
    found = await rows(
        session, "SELECT * FROM auth.privacy_requests WHERE id=:id" + (" FOR UPDATE" if lock else ""), {"id": ident}
    )
    if not found:
        raise HTTPException(404, "Privacy request not found")
    return found[0]


@router.get("/subjects")
async def subjects(
    q: str = Query(min_length=3, max_length=64),
    principal: Principal = Depends(require_admin("admin.privacy.read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {
        "items": await rows(
            session,
            "SELECT id,bank_username,display_name,status FROM auth.application_users WHERE "
            "lower(bank_username)=lower(:q) OR id::text=:q ORDER BY created_at DESC LIMIT 10",
            {"q": q},
        )
    }


@router.get("/requests")
async def requests(
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_admin("admin.privacy.read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {
        "items": await rows(
            session,
            "SELECT p.*,u.bank_username,u.display_name FROM auth.privacy_requests p "
            "JOIN auth.application_users u ON u.id=p.user_id ORDER BY p.created_at DESC,p.id OFFSET :off LIMIT 25",
            {"off": offset},
        )
    }


@router.post("/requests", status_code=201)
async def create(
    body: Create,
    principal: Principal = Depends(require_admin("admin.privacy.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, "admin.privacy.manage")
    if not await session.scalar(text("SELECT id FROM auth.application_users WHERE id=:u"), {"u": body.user_id}):
        raise HTTPException(404, "Profile not found")
    if body.ticket_id and not await session.scalar(
        text("SELECT id FROM auth.support_tickets WHERE id=:t AND user_id=:u"), {"t": body.ticket_id, "u": body.user_id}
    ):
        raise HTTPException(409, "Ticket does not belong to the data subject")
    ident = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO auth.privacy_requests(id,user_id,kind,source,ticket_id,created_by) "
            "VALUES(:id,:u,:kind,:source,:ticket,:actor)"
        ),
        {
            "id": ident,
            "u": body.user_id,
            "kind": body.kind,
            "source": body.source,
            "ticket": body.ticket_id,
            "actor": uuid.UUID(principal.user_id),
        },
    )
    audit(session, principal.user_id, "privacy.received", str(ident), {"kind": body.kind})
    await session.commit()
    return {"id": ident}


@router.get("/requests/{ident}")
async def detail(
    ident: uuid.UUID,
    principal: Principal = Depends(require_admin("admin.privacy.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    item = await case(session, ident)
    return {
        "request": item,
        "auth_tables": await auth_inventory(session),
        "finance": await core.privacy("inventory", {"user_id": str(item["user_id"])}),
        "blockers": await blockers(session, item["user_id"], ident),
    }


@router.post("/requests/{ident}/actions")
async def act(
    ident: uuid.UUID,
    body: Action,
    principal: Principal = Depends(require_admin("admin.privacy.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    grants = await administration_lock(session, principal.user_id, "admin.privacy.manage")
    if not body.confirmed or not body.reason.strip():
        raise HTTPException(422, "Confirm the action and provide its reason")
    item = await case(session, ident, True)
    if body.action == "verify":
        if item["status"] != "received":
            raise HTTPException(409, "Only a received request can be verified")
        await session.execute(
            text(
                "UPDATE auth.privacy_requests SET status='verified',verified_by=:a,verified_at=now(),reason=:r,updated_at=now() WHERE id=:id"
            ),
            {"id": ident, "a": uuid.UUID(principal.user_id), "r": body.reason},
        )
    elif body.action in {"approve", "reject"}:
        if item["status"] not in {"received", "verified", "blocked"} or (
            body.action == "approve" and not item["verified_at"]
        ):
            raise HTTPException(409, "Verify ownership before approving a request")
        if body.action == "approve" and item["kind"] == "erasure":
            if "admin.privacy.erase" not in grants:
                raise HTTPException(403, "Erasure permission required")
            if not body.review_confirmed or not body.retention_basis.strip():
                raise HTTPException(
                    422, "Review copies and disputes and record a basis for retained structural records"
                )
            await protect_last_superadmin(session, item["user_id"])
        if body.action == "approve" and item["kind"] in {"restriction", "objection", "consent_withdrawal"}:
            await protect_last_superadmin(session, item["user_id"])
        await session.execute(
            text(
                "UPDATE auth.privacy_requests SET status=:s,decided_by=:a,decided_at=now(),reason=:r,"
                "review_confirmed=:review,retention_basis=:basis,updated_at=now(),notification_status='manual_required' WHERE id=:id"
            ),
            {
                "id": ident,
                "s": "approved" if body.action == "approve" else "rejected",
                "a": uuid.UUID(principal.user_id),
                "r": body.reason,
                "review": body.review_confirmed,
                "basis": body.retention_basis,
            },
        )
    elif body.action == "complete":
        if (
            item["kind"] in {"erasure", "restriction", "objection", "consent_withdrawal"}
            or item["status"] != "approved"
            or not body.review_confirmed
        ):
            raise HTTPException(409, "Automatic processing or verified manual fulfilment is required")
        await session.execute(
            text(
                "UPDATE auth.privacy_requests SET status='completed',completed_at=now(),reason=:r,updated_at=now(),notification_status='manual_required' WHERE id=:id"
            ),
            {"id": ident, "r": body.reason},
        )
    else:
        if item["status"] not in {"completed", "rejected"}:
            raise HTTPException(409, "No final result to communicate")
        await session.execute(
            text("UPDATE auth.privacy_requests SET notification_status='notified',updated_at=now() WHERE id=:id"),
            {"id": ident},
        )
    audit(session, principal.user_id, "privacy." + body.action, str(ident), {})
    await session.commit()
    return await case(session, ident)


@router.post("/requests/{ident}/export")
async def export(
    ident: uuid.UUID,
    body: Export,
    request: Request,
    principal: Principal = Depends(require_admin("admin.privacy.export")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> JSONResponse:
    await administration_lock(session, principal.user_id, "admin.privacy.export")
    item = await case(session, ident)
    if not item["verified_at"] or item["status"] == "rejected":
        raise HTTPException(403, "Verify the data subject before export")
    if body.schema_name == "auth":
        data = await export_page(session, item["user_id"], body.table, body.offset)
    else:
        data = without_secrets(
            await core.privacy("export", {"user_id": str(item["user_id"]), "table": body.table, "offset": body.offset})
        )
    audit(
        session,
        principal.user_id,
        "privacy.export",
        str(ident),
        {"schema": body.schema_name, "table": body.table, "offset": body.offset},
    )
    await session.commit()
    return JSONResponse(
        {"classification": "staff_review_only; third-party data must be reviewed before disclosure", **data},
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="privacy-{ident}-{body.schema_name}-{body.table}-{body.offset}.json"',
        },
    )


@router.get("/holds")
async def holds(
    principal: Principal = Depends(require_admin("admin.privacy.read")), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return {
        "items": await rows(
            session, "SELECT * FROM auth.privacy_holds WHERE released_at IS NULL ORDER BY expires_at LIMIT 100"
        )
    }


@router.post("/holds", status_code=201)
async def hold(
    body: Hold,
    principal: Principal = Depends(require_admin("admin.privacy.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, "admin.privacy.manage")
    if body.expires_at.tzinfo is None or body.expires_at <= datetime.now(UTC) or not body.basis.strip():
        raise HTTPException(422, "A future timezone-aware expiry and lawful basis are required")
    if body.ticket_id and not await session.scalar(
        text("SELECT id FROM auth.support_tickets WHERE id=:id AND user_id=:u"),
        {"id": body.ticket_id, "u": body.user_id},
    ):
        raise HTTPException(409, "Ticket does not belong to the data subject")
    result = await rows(
        session,
        "INSERT INTO auth.privacy_holds(user_id,basis,expires_at,created_by,ticket_id) VALUES(:u,:b,:e,:a,:t) RETURNING id",
        {
            "u": body.user_id,
            "b": body.basis,
            "e": body.expires_at,
            "a": uuid.UUID(principal.user_id),
            "t": body.ticket_id,
        },
    )
    effective = (
        await rows(
            session,
            "SELECT expires_at,basis FROM auth.privacy_holds WHERE user_id=:u AND released_at IS NULL AND expires_at>now() ORDER BY expires_at DESC LIMIT 1",
            {"u": body.user_id},
        )
    )[0]
    await core.privacy(
        "hold",
        {"user_id": str(body.user_id), "expires_at": effective["expires_at"].isoformat(), "basis": effective["basis"]},
    )
    audit(session, principal.user_id, "privacy.hold", str(result[0]["id"]), {})
    await session.commit()
    return result[0]


@router.post("/holds/{ident}/release")
async def release(
    ident: uuid.UUID,
    principal: Principal = Depends(require_admin("admin.privacy.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, "admin.privacy.manage")
    found = await rows(
        session,
        "UPDATE auth.privacy_holds SET released_at=now() WHERE id=:id AND released_at IS NULL RETURNING user_id",
        {"id": ident},
    )
    if not found:
        raise HTTPException(404, "Active hold not found")
    remaining = await rows(
        session,
        "SELECT expires_at,basis FROM auth.privacy_holds WHERE user_id=:u AND released_at IS NULL AND expires_at>now() ORDER BY expires_at DESC LIMIT 1",
        {"u": found[0]["user_id"]},
    )
    if remaining:
        await core.privacy(
            "hold",
            {
                "user_id": str(found[0]["user_id"]),
                "expires_at": remaining[0]["expires_at"].isoformat(),
                "basis": remaining[0]["basis"],
            },
        )
    else:
        await core.privacy("release_hold", {"user_id": str(found[0]["user_id"])})
    audit(session, principal.user_id, "privacy.release_hold", str(ident), {})
    await session.commit()
    return {"released": True}
