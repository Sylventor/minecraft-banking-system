import asyncio

import pytest

from bot.app.confirmation import Confirmation


@pytest.mark.asyncio
async def test_parallel_button_presses_execute_once():
    confirmation = Confirmation(123)
    calls = []

    async def send(key):
        calls.append(key)
        await asyncio.sleep(0)
        return "operation-id"

    results = await asyncio.gather(
        *(confirmation.execute(123, send) for _ in range(10))
    )
    assert results == ["operation-id"] * 10
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_network_error_retries_with_same_key():
    confirmation = Confirmation(123)
    keys = []

    async def send(key):
        keys.append(key)
        if len(keys) == 1:
            raise OSError("response lost")
        return "operation-id"

    with pytest.raises(OSError):
        await confirmation.execute(123, send)
    assert await confirmation.execute(123, send) == "operation-id"
    assert keys[0] == keys[1]


@pytest.mark.asyncio
async def test_foreign_and_expired_confirmation_never_reaches_api():
    confirmation = Confirmation(123, timeout=0)

    async def send(key):
        pytest.fail("API must not be reached")

    with pytest.raises(PermissionError):
        await confirmation.execute(456, send)
    with pytest.raises(TimeoutError):
        await confirmation.execute(123, send)
