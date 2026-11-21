"""The deployed Uvicorn proxy boundary must preserve the real rate-limit subject."""

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,expected", [("10.0.0.2", "198.51.100.9"), ("203.0.113.7", "203.0.113.7")])
async def test_only_configured_proxy_chain_can_supply_the_client_ip(peer, expected):
    async def app(scope, receive, send):
        request = Request(scope)
        await JSONResponse({"client": request.client.host})(scope, receive, send)

    guarded = ProxyHeadersMiddleware(app, trusted_hosts=["10.0.0.2", "10.0.0.3"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guarded, client=(peer, 123)), base_url="http://test"
    ) as client:
        # An attacker-controlled leftmost address must never win over the first untrusted hop.
        result = await client.get("/", headers={"X-Forwarded-For": "192.0.2.99, 198.51.100.9, 10.0.0.3"})
        assert result.json()["client"] == expected
