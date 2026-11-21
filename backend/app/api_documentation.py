"""Public English API documentation with no CDN scripts or internal administrative schemas."""

from __future__ import annotations

import html
import json
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.responses import HTMLResponse, JSONResponse


def install_api_documentation(app: FastAPI, origin: str) -> None:
    def public_schema() -> dict[str, Any]:
        schema = get_openapi(
            title=app.title,
            version="2.6.1",
            routes=app.routes,
            description="Minecraft banking simulation using fictional DB currency. Amounts are exact decimal strings. Browser sessions use passwords or optional Discord OAuth2 with same-origin writes. Integration keys require scopes and account allowlists. Financial commands require stable idempotency keys.",
        )
        # FastAPI may retain lazy included routers without a direct .path attribute.
        # Generate first, then redact paths and every unreachable component schema.
        schema["paths"] = {
            path: value
            for path, value in schema["paths"].items()
            if path.startswith("/api/v1/")
            and not path.startswith("/api/v1/admin/")
            and not path.startswith("/api/v1/internal/")
            and path not in {"/api/v1/minecraft/verify", "/api/v1/auth/discord/callback", "/api/v1/openapi.json"}
        }
        models = schema.get("components", {}).get("schemas", {})
        reachable: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                reference = value.get("$ref", "")
                if reference.startswith("#/components/schemas/"):
                    name = reference.rsplit("/", 1)[1]
                    if name not in reachable:
                        reachable.add(name)
                        visit(models.get(name, {}))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(schema["paths"])
        schema.setdefault("components", {})["schemas"] = {
            name: value for name, value in models.items() if name in reachable
        }
        # Omit translated runtime label defaults from the English reference only.
        for model in schema["components"]["schemas"].values():
            for prop in model.get("properties", {}).values():
                default = prop.get("default")
                if isinstance(default, str) and default in {"Личный счёт", "Счёт организации"}:
                    prop.pop("default")
                    prop["description"] = "Server-provided account label when no custom name is set."
        schema["servers"] = [{"url": origin.rstrip("/"), "description": "API origin"}]
        schema.setdefault("components", {})["securitySchemes"] = {
            "ApiKey": {
                "type": "http",
                "scheme": "bearer",
                "description": "Scoped integration key; never place it in URLs or browser source",
            },
            "Session": {
                "type": "apiKey",
                "in": "cookie",
                "name": "horizon_session",
                "description": "HttpOnly session cookie; writes require the configured same-origin Origin",
            },
        }
        for path, methods in schema["paths"].items():
            for operation in methods.values():
                if isinstance(operation, dict) and path not in {
                    "/api/v1/auth/discord/login",
                    "/api/v1/auth/login",
                    "/api/v1/auth/register",
                    "/api/v1/auth/login-availability",
                    "/api/v1/terms",
                }:
                    if path == "/api/v1/news" or path.startswith("/api/v1/news/"):
                        operation.pop("security", None)
                        continue
                    operation["security"] = [{"ApiKey": []}, {"Session": []}]
                    if path.startswith("/api/v1/auth/"):
                        operation["security"] = [{"Session": []}]
                    if path.startswith("/api/v1/payment-requests/") and path.endswith(("/pay", "/decline")):
                        operation["security"] = [{"Session": []}]
                    if (
                        "/payroll/" in path
                        or path.endswith("/business-permissions")
                        or (
                            "/analytics/" in path and path != "/api/v1/organizations/{organization_id}/analytics/{kind}"
                        )
                    ):
                        operation["security"] = [{"Session": []}]
                    if path.startswith("/api/v1/exchange/stocks"):
                        operation.pop("security", None)
                    elif path.startswith(
                        (
                            "/api/v1/exchange/issuers",
                            "/api/v1/exchange/ticker-availability/",
                            "/api/v1/exchange/dividends",
                            "/api/v1/me/sessions",
                            "/api/v1/auth/session/",
                        )
                    ):
                        operation["security"] = [{"Session": []}]
        return schema

    app.openapi = public_schema  # type: ignore[method-assign]

    @app.get("/api/openapi.json", include_in_schema=False)
    async def schema_response() -> JSONResponse:
        return JSONResponse(public_schema())

    @app.get("/api/docs", include_in_schema=False, response_class=HTMLResponse)
    async def documentation() -> HTMLResponse:
        schema = public_schema()
        paths = []
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                parameters = json.dumps(
                    {
                        "parameters": operation.get("parameters", []),
                        "requestBody": operation.get("requestBody"),
                        "responses": operation.get("responses", {}),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                paths.append(
                    "<details><summary><span>"
                    + html.escape(method.upper())
                    + "</span> "
                    + html.escape(path)
                    + "</summary><pre>"
                    + html.escape(parameters)
                    + "</pre></details>"
                )
        body = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Minecraft Banking System API</title>
<style>body{font:16px/1.65 system-ui;max-width:1040px;margin:auto;padding:32px;color:#172439;background:#f7f9fc}pre{overflow:auto;padding:20px;background:#eaf0f8;border-radius:10px}summary{padding:12px;cursor:pointer;overflow-wrap:anywhere}h2{margin-top:32px}</style>
<main><nav><a href="/">Web Dashboard</a> · <a href="/api/openapi.json">OpenAPI JSON</a> · <a href="#endpoints">Endpoints</a></nav>
<h1>Minecraft Banking System API</h1><p>A Minecraft banking simulation using fictional in-game currency. It is not a real financial institution. The Financial Core owns monetary operations and final account authorization.</p>
<h2>Authentication</h2><p>Use a password session or optional Discord OAuth2 in the dashboard. Cookie writes require the configured same-origin Origin. Integrations use Authorization: Bearer with independently scoped, account-allowlisted keys. Keys are shown once and must remain outside source control and browser bundles. Administrative actions require an interactive session and the corresponding permissions.</p>
<h2>Exact amounts and retries</h2><p>Amounts are strings such as 125.50, with exactly two fractional digits; the maximum is 9999999999999999.99 DB. Fees are currently zero. Reuse a stable Idempotency-Key only for the identical request. An uncertain response is not a reason to create another payment. Error objects contain error, code, and request_id.</p>
<h2>Transfers and payment requests</h2><p>Select a recipient, create a transfer quote, review the snapshot, and confirm its stable identifier. Internal transfers additionally check both accessible accounts. A merchant payment request proposes a payment without debiting the payer. Merchant keys belong to one organization and cannot authorize payment themselves. Deliver goods only after status=paid and a committed transaction ID.</p>
<h2>Organizations and payroll</h2><p>Membership and business permissions are separate from bank administration. Owners can approve their own payroll batches and automatic mandates; other users require an independent authorized approver. Scheduled work runs in a dedicated worker. Analytics read the ledger; cash flow is not accounting profit. CSV exports escape formula prefixes and support pagination.</p>
<h2>Virtual exchange</h2><p>Public market data is readable without account access. Portfolio and trading routes require their scopes and current core permissions. Limit orders reserve money or shares. Market IOC orders require price bounds, and BUY requires a budget. Unfilled remainders are cancelled. These are fictional game shares, not real securities.</p>
<h2>Minecraft and HPay</h2><p>Linking verifies a short-lived code delivered in Minecraft and binds nickname plus UUID to a profile. HPay product proposals snapshot price and merchant before a second command confirms through the same ledger. The optional Fabric client only sends normal private-message commands.</p>
<h2>Sessions</h2><p>Sessions are represented by hashes in PostgreSQL, with an inactivity lifetime up to 30 days and an absolute lifetime up to 90 days. Refresh rotates the token. Revocation blocks subsequent requests. Session/credential management requires an interactive login.</p>
<h2>Examples</h2><p>Set the API URL and key in private environment variables. The generated endpoint/schema details below define exact request fields, methods, and pagination.</p>"""
        samples = {
            "cURL": 'curl "$HORIZON_API_URL/api/v1/accounts" \\\n  -H "Authorization: Bearer $HORIZON_API_KEY"',
            "Python": 'import os, httpx\nresponse = httpx.get(os.environ["HORIZON_API_URL"] + "/api/v1/accounts", headers={"Authorization": "Bearer " + os.environ["HORIZON_API_KEY"]})\nresponse.raise_for_status()\nprint(response.json())',
            "JavaScript (server)": 'const response = await fetch(process.env.HORIZON_API_URL + "/api/v1/accounts", {headers: {Authorization: `Bearer ${process.env.HORIZON_API_KEY}`}});\nif (!response.ok) throw new Error("Request rejected");\nconsole.log(await response.json());',
        }
        for title, sample in samples.items():
            body += "<h3>" + title + "</h3><pre>" + html.escape(sample) + "</pre>"
        body += (
            '<h2 id="endpoints">API endpoints</h2>'
            + "".join(paths)
            + "<h2>Data schemas</h2><pre>"
            + html.escape(json.dumps(schema.get("components", {}).get("schemas", {}), ensure_ascii=False, indent=2))
            + "</pre><p>Internal service routes, administrative operations, and the trusted verifier are excluded from this public schema.</p></main></html>"
        )
        return HTMLResponse(body)
