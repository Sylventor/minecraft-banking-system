"""Dedicated Compose worker; no payroll loop is started in FastAPI workers."""

from __future__ import annotations

import asyncio
import signal
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from .business_calendar import next_occurrence
from .config import get_settings
from .grpc.client import CoreError, GrpcBankingClient

ACTOR = "00000000-0000-0000-0000-000000000024"
log = structlog.get_logger()


async def call(
    core: GrpcBankingClient, actor: str, org: str, action: str, target: str = "", data: dict[str, Any] | None = None
) -> dict[str, Any]:
    rid = str(uuid.uuid4())
    return await core.business(actor, org, action, target, data or {}, rid, rid, source="payroll-worker")


async def tick(core: GrpcBankingClient, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    jobs = await call(core, ACTOR, "", "worker.jobs")
    done = 0
    for schedule in jobs["schedules"]:
        try:
            anchor = datetime.fromisoformat(schedule["next_run_at"])
            next_run = next_occurrence(schedule, max(now, anchor), anchor)
            await call(
                core,
                ACTOR,
                schedule["organization_id"],
                "schedule.fire",
                schedule["id"],
                {"scheduled_for": anchor.isoformat(), "next_run_at": next_run.isoformat()},
            )
            done += 1
        except CoreError as error:
            # Another worker can have consumed exactly this occurrence. Do not
            # pause a legitimate schedule on a competing-worker conflict.
            if error.code in {"PERMISSION_DENIED", "INVALID_ARGUMENT"}:
                await call(
                    core,
                    ACTOR,
                    schedule["organization_id"],
                    "worker.schedule_error",
                    schedule["id"],
                    {"reason": "permission_or_configuration"},
                )
        except (ValueError, KeyError):
            await call(
                core,
                ACTOR,
                schedule["organization_id"],
                "worker.schedule_error",
                schedule["id"],
                {"reason": "calendar_error"},
            )
    # Re-read: a newly created automatic batch must be recoverable even if this
    # process dies between creating its occurrence and requesting execution.
    jobs = await call(core, ACTOR, "", "worker.jobs")
    for batch in jobs["batches"]:
        try:
            await call(
                core, batch["actor"], batch["organization_id"], batch["action"], batch["id"], {"confirmed": True}
            )
            done += 1
        except CoreError as error:
            if error.code in {"PERMISSION_DENIED", "FAILED_PRECONDITION", "INVALID_ARGUMENT", "RESOURCE_EXHAUSTED"}:
                await call(
                    core,
                    ACTOR,
                    batch["organization_id"],
                    "worker.batch_error",
                    batch["id"],
                    {"reason": "execution_error"},
                )
    return done


async def run() -> None:
    core = GrpcBankingClient(get_settings())
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, stopped.set)
        except NotImplementedError:
            pass
    try:
        while not stopped.is_set():
            try:
                await tick(core)
            except Exception:
                log.warning("payroll_worker_retry")
            try:
                await asyncio.wait_for(stopped.wait(), timeout=5)
            except TimeoutError:
                pass
    finally:
        await core.close()


if __name__ == "__main__":
    asyncio.run(run())
