from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any, cast

import grpc

from ..config import Settings


class CoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def fingerprint(operation: str, actor_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "actor_id": actor_id, "payload": payload}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class GrpcBankingClient:
    def __init__(self, settings: Settings) -> None:
        try:
            from horizon.banking.v1 import banking_pb2, banking_pb2_grpc
        except ImportError as error:
            raise RuntimeError("gRPC bindings are absent; run `make proto` or build the backend image") from error
        self.pb = banking_pb2
        self.channel = grpc.aio.insecure_channel(settings.grpc_target)
        stub_factory: Any = banking_pb2_grpc.BankingServiceStub
        self.stub = stub_factory(self.channel)
        self.token = settings.internal_grpc_token.get_secret_value()

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return (("x-horizon-internal-token", self.token),)

    async def atm(self, action: str, target: str, payload: dict[str, Any], rid: str, cid: str) -> dict[str, Any]:
        value = await self._call(
            self.stub.Atm,
            self.pb.BusinessRequest(
                context=self._context("00000000-0000-0000-0000-000000000027", rid, cid, "atm"),
                action=action,
                target_id=target,
                payload=json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False),
            ),
        )
        return cast(dict[str, Any], json.loads(value.json))

    async def privacy(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        rid = str(uuid.uuid4())
        value = await self._call(self.stub.Privacy, self.pb.BusinessRequest(
            context=self._context("00000000-0000-0000-0000-000000000028", rid, rid, "privacy"),
            action=action,payload=json.dumps(payload,sort_keys=True,ensure_ascii=True,allow_nan=False)))
        return cast(dict[str, Any],json.loads(value.json))

    async def business(
        self,
        actor: str,
        org: str,
        action: str,
        target: str,
        payload: dict[str, Any],
        rid: str,
        cid: str,
        *,
        source: str = "rest",
        allowed_accounts: list[str] | None = None,
        scoped: bool = False,
    ) -> dict[str, Any]:
        value = await self._call(
            self.stub.Business,
            self.pb.BusinessRequest(
                context=self._context(actor, rid, cid, source),
                organization_id=org,
                action=action,
                target_id=target,
                payload=json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False),
                allowed_account_ids=allowed_accounts or [],
                resource_scoped=scoped,
            ),
        )
        return cast(dict[str, Any], json.loads(value.json))

    async def exchange(
        self,
        actor: str,
        action: str,
        target: str,
        org: str,
        payload: dict[str, Any],
        rid: str,
        cid: str,
        *,
        source: str = "rest",
        allowed_accounts: list[str] | None = None,
        scoped: bool = False,
    ) -> dict[str, Any]:
        value = await self._call(
            self.stub.Exchange,
            self.pb.BusinessRequest(
                context=self._context(actor, rid, cid, source),
                action=action,
                target_id=target,
                organization_id=org,
                payload=json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False),
                allowed_account_ids=allowed_accounts or [],
                resource_scoped=scoped,
            ),
        )
        return cast(dict[str, Any], json.loads(value.json))

    async def payment_requests(self, actor: str, rid: str, cid: str, **payload: Any) -> dict[str, Any]:
        value = await self._call(
            self.stub.PaymentRequests,
            self.pb.PaymentRequestCommand(context=self._context(actor, rid, cid), **payload),
        )
        return cast(dict[str, Any], json.loads(value.json))

    async def administrative_query(self, actor: str, rid: str, cid: str, **filters: Any) -> dict[str, Any]:
        value = await self._call(
            self.stub.AdministrativeQuery, self.pb.AdminQueryRequest(context=self._context(actor, rid, cid), **filters)
        )
        return cast(dict[str, Any], json.loads(value.json))

    async def administrative_change(self, actor: str, rid: str, cid: str, **payload: Any) -> dict[str, Any]:
        value = await self._call(
            self.stub.AdministrativeChange,
            self.pb.AdminChangeRequest(context=self._context(actor, rid, cid), **payload),
        )
        return cast(dict[str, Any], json.loads(value.json))

    async def notification_events(self) -> dict[str, Any]:
        context = self._context(
            "00000000-0000-0000-0000-000000000022", "notification-relay", "notification-relay", "notification-relay"
        )
        value = await self._call(self.stub.NotificationEvents, self.pb.NotificationEventsRequest(context=context))
        return cast(dict[str, Any], json.loads(value.json))

    async def acknowledge_notification_events(self, ids: list[int]) -> None:
        context = self._context(
            "00000000-0000-0000-0000-000000000022", "notification-relay", "notification-relay", "notification-relay"
        )
        await self._call(
            self.stub.AcknowledgeNotificationEvents,
            self.pb.NotificationEventsRequest(context=context, acknowledged_ids=ids),
        )

    def _context(self, actor_id: str, request_id: str, correlation_id: str, source: str = "rest") -> Any:
        return self.pb.RequestContext(
            actor_id=actor_id, request_id=request_id, correlation_id=correlation_id, source=source
        )

    def _operation(self, value: Any) -> dict[str, Any]:
        return {
            "id": value.id,
            "type": value.type,
            "status": value.status,
            "source_account_id": value.source_account_id,
            "destination_account_id": value.destination_account_id,
            "amount": {"amount": value.amount.amount, "currency": value.amount.currency},
            "created_at": value.created_at,
            "completed_at": value.completed_at,
            "related_operation_id": value.related_operation_id,
            "hold_id": value.hold_id,
            "reason": value.reason,
            "fee": {"amount": value.fee.amount or "0.00", "currency": "DB"},
            "source_public_number": value.source_public_number,
            "destination_public_number": value.destination_public_number,
            "source_owner_id": value.source_owner_id,
            "destination_owner_id": value.destination_owner_id,
            "source_name": value.source_name,
            "destination_name": value.destination_name,
            "source_organization_id": value.source_organization_id,
            "destination_organization_id": value.destination_organization_id,
        }

    @staticmethod
    def _account(value: Any) -> dict[str, Any]:
        return {
            "id": value.id,
            "status": value.status,
            "account_type": value.account_type,
            "balance": {"amount": value.balance.amount, "currency": "DB"},
            "held": {"amount": value.held.amount, "currency": "DB"},
            "available": {"amount": value.available.amount, "currency": "DB"},
            "public_number": value.public_number,
            "name": value.name,
            "is_primary": value.is_primary,
            "owner_id": value.owner_id,
            "organization_id": value.organization_id,
            "permissions": list(value.permissions),
        }

    async def primary_account(self, actor: str, rid: str, cid: str) -> dict[str, Any]:
        return self._account(
            await self._call(
                self.stub.EnsurePrimaryAccount, self.pb.PrimaryAccountRequest(context=self._context(actor, rid, cid))
            )
        )

    async def request_outcome(self, actor: str, key: str, rid: str, cid: str) -> dict[str, Any]:
        return self._operation(
            await self._call(
                self.stub.GetRequestOutcome,
                self.pb.RequestOutcomeRequest(
                    context=self._context(actor, rid, cid), idempotency_key=key, operation_type="transfer"
                ),
            )
        )

    async def update_account(
        self, actor: str, account_id: str, name: str, primary: bool, rid: str, cid: str
    ) -> dict[str, Any]:
        return self._account(
            await self._call(
                self.stub.UpdateAccount,
                self.pb.UpdateAccountRequest(
                    context=self._context(actor, rid, cid), account_id=account_id, name=name, set_primary=primary
                ),
            )
        )

    async def resolve_account(
        self, actor: str, rid: str, cid: str, identifier: str = "", owner_id: str = ""
    ) -> dict[str, Any]:
        value = await self._call(
            self.stub.ResolveAccount,
            self.pb.PublicAccountRequest(
                context=self._context(actor, rid, cid), identifier=identifier, owner_id=owner_id
            ),
        )
        return {
            "account_id": value.account_id,
            "public_number": value.public_number,
            "name": value.name,
            "owner_id": value.owner_id,
            "organization_id": value.organization_id,
        }

    async def activity(
        self,
        actor: str,
        rid: str,
        cid: str,
        *,
        account_id: str = "",
        limit: int = 20,
        cursor: str = "",
        operation_type: str = "",
        query: str = "",
        from_date: str = "",
        to_date: str = "",
        order: str = "desc",
    ) -> dict[str, Any]:
        value = await self._call(
            self.stub.GetActivity,
            self.pb.ActivityRequest(
                context=self._context(actor, rid, cid),
                account_id=account_id,
                limit=limit,
                cursor=cursor,
                operation_type=operation_type,
                query=query,
                from_date=from_date,
                to_date=to_date,
                order=order,
            ),
        )
        return {"items": [self._operation(item) for item in value.operations], "next_cursor": value.next_cursor or None}

    def _organization(self, value: Any) -> dict[str, Any]:
        return {
            "id": value.id,
            "name": value.name,
            "owner_id": value.owner_id,
            "current_role": value.current_role,
            "members": [
                {"user_id": m.user_id, "role": m.role, "permissions": list(m.permissions)} for m in value.members
            ],
            "accounts": [self._account(a) for a in value.accounts],
        }

    async def organization(self, actor: str, org: str, rid: str, cid: str) -> dict[str, Any]:
        return self._organization(
            await self._call(
                self.stub.GetOrganization,
                self.pb.OrganizationRequest(context=self._context(actor, rid, cid), organization_id=org),
            )
        )

    async def ensure_organization(
        self, owner: str, org: str, name: str, members: list[dict[str, Any]], rid: str, cid: str
    ) -> None:
        await self._call(
            self.stub.EnsureOrganization,
            self.pb.OrganizationRequest(
                context=self._context(owner, rid, cid),
                organization_id=org,
                owner_id=owner,
                name=name,
                members=[
                    self.pb.OrganizationMember(
                        user_id=m["user_id"],
                        role=m["role"],
                        permissions=m.get("permissions", ["accounts.read", "balance.read", "history.read"]),
                    )
                    for m in members
                ],
            ),
        )

    async def organization_account(
        self, actor: str, org: str, name: str, key: str, rid: str, cid: str
    ) -> dict[str, Any]:
        return self._account(
            await self._call(
                self.stub.CreateOrganizationAccount,
                self.pb.OrganizationAccountRequest(
                    context=self._context(actor, rid, cid), organization_id=org, name=name, idempotency_key=key
                ),
            )
        )

    async def organization_member(
        self, actor: str, org: str, member: dict[str, Any], remove: bool, rid: str, cid: str
    ) -> dict[str, Any]:
        value = await self._call(
            self.stub.ChangeOrganizationMember,
            self.pb.OrganizationMemberRequest(
                context=self._context(actor, rid, cid),
                organization_id=org,
                member=self.pb.OrganizationMember(
                    user_id=member["user_id"], role=member["role"], permissions=member.get("permissions", [])
                ),
                remove=remove,
            ),
        )
        return self._organization(value)

    async def _call(self, method: Any, request: Any) -> Any:
        try:
            return await method(request, metadata=self._metadata(), timeout=5)
        except grpc.aio.AioRpcError as error:
            raise CoreError(error.code().name, error.details() or "core request failed") from error

    async def balance(self, actor: str, account_id: str, request_id: str, correlation_id: str) -> dict[str, Any]:
        response = await self._call(
            self.stub.GetBalance,
            self.pb.GetBalanceRequest(context=self._context(actor, request_id, correlation_id), account_id=account_id),
        )
        return self._account(response)

    async def create_account(
        self, actor: str, account_type: str, request_id: str, correlation_id: str, name: str = "", key: str = ""
    ) -> dict[str, Any]:
        response = await self._call(
            self.stub.CreateAccount,
            self.pb.CreateAccountRequest(
                context=self._context(actor, request_id, correlation_id),
                account_type=account_type,
                name=name,
                idempotency_key=key,
            ),
        )
        return self._account(response)

    async def ready(self) -> None:
        await self.channel.channel_ready()
        # A real query proves that the core's financial database is reachable too.
        await self._call(
            self.stub.CheckAuthorization,
            self.pb.AuthorizationRequest(
                context=self._context("00000000-0000-0000-0000-000000000000", "readiness", "readiness"),
                account_id="00000000-0000-0000-0000-000000000000",
                permission="accounts.read",
            ),
        )

    async def history(
        self, actor: str, account_id: str, limit: int, request_id: str, correlation_id: str, cursor: str = ""
    ) -> dict[str, Any]:
        response = await self._call(
            self.stub.GetHistory,
            self.pb.HistoryRequest(
                context=self._context(actor, request_id, correlation_id),
                account_id=account_id,
                limit=limit,
                cursor=cursor,
            ),
        )
        return {
            "items": [self._operation(item) for item in response.operations],
            "next_cursor": response.next_cursor or None,
        }

    async def operation(self, actor: str, operation_id: str, request_id: str, correlation_id: str) -> dict[str, Any]:
        response = await self._call(
            self.stub.GetOperation,
            self.pb.GetOperationRequest(
                context=self._context(actor, request_id, correlation_id), operation_id=operation_id
            ),
        )
        return self._operation(response)

    async def accounts(self, actor: str, limit: int, cursor: str, rid: str, cid: str) -> dict[str, Any]:
        response = await self._call(
            self.stub.ListAccounts,
            self.pb.ListAccountsRequest(context=self._context(actor, rid, cid), limit=limit, cursor=cursor),
        )
        return {
            "items": [self._account(row) for row in response.accounts],
            "next_cursor": response.next_cursor or None,
        }

    async def money_operation(
        self,
        method: str,
        actor: str,
        payload: dict[str, Any],
        idempotency_key: str,
        request_id: str,
        correlation_id: str,
        source: str = "rest",
    ) -> dict[str, Any]:
        request = self.pb.OperationRequest(
            context=self._context(actor, request_id, correlation_id, source),
            source_account_id=payload.get("source_account_id", ""),
            destination_account_id=payload.get("destination_account_id", payload.get("target_account_id", "")),
            amount=self.pb.Money(amount=payload["amount"], currency="DB"),
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint(method, actor, payload),
            reason=payload.get("reason", ""),
            related_operation_id=payload.get("original_operation_id", ""),
            authority_reference=payload.get("authority_reference", ""),
            not_after=payload.get("expires_at", ""),
        )
        response = await self._call(getattr(self.stub, method), request)
        return self._operation(response)

    async def hold(
        self,
        actor: str,
        payload: dict[str, Any],
        idempotency_key: str,
        request_id: str,
        correlation_id: str,
        source: str = "rest",
    ) -> dict[str, Any]:
        request = self.pb.HoldRequest(
            context=self._context(actor, request_id, correlation_id, source),
            account_id=payload["account_id"],
            amount=self.pb.Money(amount=payload["amount"], currency="DB"),
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint("hold", actor, payload),
            reason=payload.get("reason", ""),
        )
        return self._operation(await self._call(self.stub.CreateHold, request))

    async def close(self) -> None:
        await self.channel.close(None)

    async def hold_action(
        self,
        method: str,
        actor: str,
        hold_id: str,
        payload: dict[str, Any],
        key: str,
        rid: str,
        cid: str,
        source: str = "rest",
    ) -> dict[str, Any]:
        request = self.pb.HoldActionRequest(
            context=self._context(actor, rid, cid, source),
            hold_id=hold_id,
            destination_account_id=payload.get("destination_account_id") or "",
            idempotency_key=key,
            request_fingerprint=fingerprint(method, actor, {"hold_id": hold_id, **payload}),
            reason=payload.get("reason", ""),
        )
        return self._operation(await self._call(getattr(self.stub, method), request))


class BankingClientProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: GrpcBankingClient | None = None
        self.lock = asyncio.Lock()

    async def get(self) -> GrpcBankingClient:
        async with self.lock:
            if self.client is None:
                self.client = GrpcBankingClient(self.settings)
            return self.client
