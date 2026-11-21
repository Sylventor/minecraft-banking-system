"""Short real-core tests: reservations, exact evidence, replay and crash fencing."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest
from test_core_finance import balance, context, fund, invoke, pb, reconciled
from test_core_finance import core as core_fixture

core = core_fixture
SERVICE = "00000000-0000-0000-0000-000000000027"


def call(core, action, data, target=""):
    ctx = context(SERVICE)
    ctx.source = "atm"
    return json.loads(
        invoke(
            core.Atm,
            pb.BusinessRequest(
                context=ctx, action=action, target_id=target, payload=json.dumps(data)
            ),
        ).json
    )


def runtime(core, db):
    db.execute("UPDATE finance.atm_runtime SET expires_at=now()-interval '1 second'")
    db.commit()
    instance, user, player = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    call(
        core,
        "lease",
        {"instance_id": instance, "max_amount": 64, "player_distance": 15},
    )
    account = invoke(
        core.EnsurePrimaryAccount, pb.PrimaryAccountRequest(context=context(user))
    ).id
    devices = []
    for n, x in [("far", 0), ("near", 8)]:
        devices.append(
            {
                "id": n + uuid.uuid4().hex[:8],
                "username": "HorizonMaster" if n == "far" else "HorizonATM",
                "master": n == "far",
                "connected": True,
                "position": {"x": x, "y": 64, "z": 0},
                "dimension": "overworld",
                "inventory": 32,
                "free_space": 100,
                "observations": {
                    "playername": {
                        "uuid": player,
                        "x": 7,
                        "y": 64,
                        "z": 0,
                        "dimension": "overworld",
                        "age_ms": 0,
                    }
                },
                "container": {"dimension": "overworld", "x": x, "y": 64, "z": 1},
                "error": "",
            }
        )
    call(core, "heartbeat", {"instance_id": instance, "devices": devices})
    return instance, user, player, account, devices


def prepare(core, state, kind="withdraw", quantity=5):
    instance, user, player, _, _ = state
    return call(
        core,
        "prepare",
        {
            "instance_id": instance,
            "user_id": user,
            "player_uuid": player,
            "nickname": "PlayerName",
            "kind": kind,
            "quantity": quantity,
            "request_key": str(uuid.uuid4()),
        },
    )


def test_atm_withdraw_deposit_exact_settlement_and_parallel_replay(core, database):
    state = runtime(core, database)
    instance, user, _, account, devices = state
    fund(core, database, user, account, "10.00")
    op = prepare(core, state)
    assert op["device_id"] == devices[1]["id"]
    assert balance(core, user, account).available.amount == "5.00"
    assert balance(core, user, account).balance.amount == "10.00"
    proof = {
        "inventory_before": 32,
        "container_before": 0,
        "inventory_after": 27,
        "container_after": 5,
    }
    call(
        core,
        "start",
        {
            "instance_id": instance,
            "evidence": {"inventory_before": 32, "container_before": 0},
        },
        op["id"],
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: call(
                    core,
                    "confirm",
                    {"instance_id": instance, "evidence": proof},
                    op["id"],
                ),
                range(2),
            )
        )
    assert results[0]["operation_id"] == results[1]["operation_id"]
    assert balance(core, user, account).balance.amount == "5.00"
    op = prepare(core, state, "deposit", 3)
    proof = {
        "inventory_before": 27,
        "container_before": 3,
        "inventory_after": 30,
        "container_after": 0,
    }
    call(
        core,
        "start",
        {
            "instance_id": instance,
            "evidence": {"inventory_before": 27, "container_before": 3},
        },
        op["id"],
    )
    assert (
        call(core, "confirm", {"instance_id": instance, "evidence": proof}, op["id"])[
            "status"
        ]
        == "COMPLETED"
    )
    assert balance(core, user, account).balance.amount == "8.00"
    reconciled(database)


def test_atm_safe_failure_releases_hold_but_uncertain_movement_retains_it(
    core, database
):
    state = runtime(core, database)
    instance, user, _, account, _ = state
    fund(core, database, user, account, "10.00")
    safe = prepare(core, state)
    assert (
        call(
            core,
            "fail",
            {"instance_id": instance, "evidence": {}, "error": "Контейнер недоступен"},
            safe["id"],
        )["status"]
        == "FAILED"
    )
    assert balance(core, user, account).available.amount == "10.00"
    uncertain = prepare(core, state)
    call(
        core,
        "start",
        {
            "instance_id": instance,
            "evidence": {"inventory_before": 32, "container_before": 0},
        },
        uncertain["id"],
    )
    with pytest.raises(grpc.RpcError):
        call(
            core,
            "confirm",
            {
                "instance_id": instance,
                "evidence": {
                    "inventory_before": 32,
                    "container_before": 0,
                    "inventory_after": 27,
                    "container_after": 4,
                },
            },
            uncertain["id"],
        )
    assert (
        call(
            core,
            "fail",
            {"instance_id": instance, "evidence": {}, "error": "Частичное перемещение"},
            uncertain["id"],
        )["status"]
        == "REQUIRES_REVIEW"
    )
    assert balance(core, user, account).held.amount == "5.00"
    with pytest.raises(grpc.RpcError):
        prepare(core, state)
    call(
        core,
        "admin.resolve",
        {
            "admin_id": user,
            "decision": "cancel",
            "reason": "Предметы возвращены оператором",
        },
        uncertain["id"],
    )
    assert balance(core, user, account).available.amount == "10.00"
    reconciled(database)


def test_atm_restart_fences_old_physical_commands_and_never_reissues(core, database):
    state = runtime(core, database)
    instance, user, _, account, _ = state
    fund(core, database, user, account, "10.00")
    op = prepare(core, state)
    call(
        core,
        "start",
        {
            "instance_id": instance,
            "evidence": {"inventory_before": 32, "container_before": 0},
        },
        op["id"],
    )
    with pytest.raises(grpc.RpcError):
        call(
            core,
            "lease",
            {"instance_id": str(uuid.uuid4()), "max_amount": 64, "player_distance": 15},
        )
    database.execute(
        "UPDATE finance.atm_runtime SET expires_at=now()-interval '1 second'"
    )
    database.commit()
    new_instance = str(uuid.uuid4())
    call(
        core,
        "lease",
        {"instance_id": new_instance, "max_amount": 64, "player_distance": 15},
    )
    assert (
        call(core, "get", {"instance_id": new_instance}, op["id"])["status"]
        == "REQUIRES_REVIEW"
    )
    with pytest.raises(grpc.RpcError):
        call(core, "start", {"instance_id": instance, "evidence": {}}, op["id"])
    with pytest.raises(grpc.RpcError):
        call(core, "start", {"instance_id": new_instance, "evidence": {}}, op["id"])
    assert balance(core, user, account).held.amount == "5.00"
    call(
        core,
        "admin.resolve",
        {
            "admin_id": user,
            "decision": "settle",
            "reason": "Полная выдача подтверждена оператором",
        },
        op["id"],
    )
    assert balance(core, user, account).balance.amount == "5.00"
    reconciled(database)


def test_atm_rejects_wrong_service_context_and_missing_coordinates(core, database):
    state = runtime(core, database)
    instance, _, _, _, devices = state
    with pytest.raises(grpc.RpcError) as error:
        invoke(
            core.Atm,
            pb.BusinessRequest(
                context=context(str(uuid.uuid4())), action="status", payload="{}"
            ),
        )
    assert error.value.code() == grpc.StatusCode.PERMISSION_DENIED
    for d in devices:
        d["observations"] = {}
    call(core, "heartbeat", {"instance_id": instance, "devices": devices})
    with pytest.raises(grpc.RpcError):
        prepare(core, state, "deposit")


@pytest.mark.parametrize("inventory,quantity,container_before", [(32, 32, 0), (2304, 1728, 0), (2304, 5, 1723)])
def test_atm_collection_preserves_balance_and_disabled_device_is_not_selected(
    core, database, inventory, quantity, container_before
):
    state = runtime(core, database)
    instance, user, _, account, devices = state
    devices[1]["inventory"] = inventory
    call(core, "heartbeat", {"instance_id": instance, "devices": devices})
    op = prepare(core, state, "collection", quantity)
    assert op["quantity"] == quantity
    call(
        core,
        "start",
        {
            "instance_id": instance,
            "evidence": {"inventory_before": inventory, "container_before": container_before},
        },
        op["id"],
    )
    completed = call(
        core,
        "confirm",
        {
            "instance_id": instance,
            "evidence": {
                "inventory_before": inventory,
                "container_before": container_before,
                "inventory_after": inventory - quantity,
                "container_after": container_before + quantity,
            },
        },
        op["id"],
    )
    assert completed["status"] == "COMPLETED" and completed["operation_id"] is None
    assert completed["quantity"] == quantity
    assert balance(core, user, account).balance.amount == "0.00"
    call(core, "admin.enabled", {"admin_id": user, "enabled": False}, devices[1]["id"])
    call(core, "heartbeat", {"instance_id": instance, "devices": devices})
    prepared = prepare(core, state, "deposit", 1)
    assert prepared["device_id"] == devices[0]["id"]
    call(
        core,
        "fail",
        {"instance_id": instance, "error": "Не начата", "evidence": {}},
        prepared["id"],
    )
    reconciled(database)


@pytest.mark.parametrize("quantity", [0, 33, 2305])
def test_atm_collection_rejects_zero_excess_inventory_and_excess_capacity(core, database, quantity):
    state = runtime(core, database)
    with pytest.raises(grpc.RpcError):
        prepare(core, state, "collection", quantity)
    reconciled(database)


def test_atm_unstarted_timeout_safely_releases_reservation(core, database):
    state = runtime(core, database)
    instance, user, _, account, devices = state
    fund(core, database, user, account, "10.00")
    op = prepare(core, state)
    database.execute(
        "UPDATE finance.atm_operations SET updated_at=now()-interval '46 seconds' WHERE id=%s",
        (op["id"],),
    )
    database.commit()
    call(core, "heartbeat", {"instance_id": instance, "devices": devices})
    assert call(core, "get", {"instance_id": instance}, op["id"])["status"] == "FAILED"
    assert balance(core, user, account).available.amount == "10.00"
    reconciled(database)


def test_atm_renamed_device_cannot_bypass_quarantine_of_physical_inventory(
    core, database
):
    state = runtime(core, database)
    instance, user, _, account, devices = state
    fund(core, database, user, account, "10.00")
    op = prepare(core, state)
    call(
        core,
        "start",
        {
            "instance_id": instance,
            "evidence": {"inventory_before": 32, "container_before": 0},
        },
        op["id"],
    )
    call(
        core,
        "fail",
        {
            "instance_id": instance,
            "error": "Неизвестен результат выдачи",
            "evidence": {},
        },
        op["id"],
    )
    other, player = str(uuid.uuid4()), str(uuid.uuid4())
    invoke(core.EnsurePrimaryAccount, pb.PrimaryAccountRequest(context=context(other)))
    devices[0]["connected"] = False
    for device in devices:
        device["id"] = "renamed" + uuid.uuid4().hex[:8]
        device["observations"] = {
            "playertwo": {
                "uuid": player,
                "x": 7,
                "y": 64,
                "z": 0,
                "dimension": "overworld",
                "age_ms": 0,
            }
        }
    call(core, "heartbeat", {"instance_id": instance, "devices": devices})
    with pytest.raises(grpc.RpcError):
        call(
            core,
            "prepare",
            {
                "instance_id": instance,
                "user_id": other,
                "player_uuid": player,
                "nickname": "PlayerTwo",
                "kind": "deposit",
                "quantity": 1,
                "request_key": str(uuid.uuid4()),
            },
        )
    assert balance(core, user, account).held.amount == "5.00"
    call(
        core,
        "admin.resolve",
        {
            "admin_id": user,
            "decision": "cancel",
            "reason": "Оператор вернул предметы и проверил инвентарь",
        },
        op["id"],
    )
    reconciled(database)
