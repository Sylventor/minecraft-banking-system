"""Dedicated expiry service; matching and dividends are synchronous atomic core commands."""

import asyncio
import signal
import uuid

import structlog

from .config import get_settings
from .grpc.client import GrpcBankingClient


async def run() -> None:
    core = GrpcBankingClient(get_settings())
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        except NotImplementedError:
            pass
    try:
        while not stopped.is_set():
            try:
                rid = str(uuid.uuid4())
                await core.exchange(
                    "00000000-0000-0000-0000-000000000025",
                    "maintenance",
                    "",
                    "",
                    {},
                    rid,
                    rid,
                    source="exchange-worker",
                )
            except Exception:
                structlog.get_logger().warning("exchange_expiry_retry")
            try:
                await asyncio.wait_for(stopped.wait(), timeout=5)
            except TimeoutError:
                pass
    finally:
        await core.close()


if __name__ == "__main__":
    asyncio.run(run())
