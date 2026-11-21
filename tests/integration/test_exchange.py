"""Actual C++/PostgreSQL exchange settlement, never production or Minecraft."""

import concurrent.futures
import json
import uuid
from datetime import UTC, datetime, timedelta

import grpc
import pytest
from test_api_security import api_client as api_fixture, session_cookie
from test_core_finance import account, authority, context, fund, invoke, pb, reconciled
from test_core_finance import core as core_fixture
from test_payment_requests import ORIGIN, setup

api_client = api_fixture
core = core_fixture


@pytest.mark.asyncio
async def test_ticker_availability_includes_unpublished_drafts_without_exposing_them(
    api_client, core
):
    s, _, _, _ = await market_setup(api_client, core)
    s[0].cookies.set("horizon_session", s[3])
    ticker = "D" + uuid.uuid4().hex[:7].upper()
    before = await s[0].get(f"/api/v1/exchange/ticker-availability/{ticker}")
    assert before.status_code == 200 and before.json() == {"available": True}
    created = await command(
        s,
        "exchange/issuers",
        {
            "organization_id": s[8],
            "account_id": s[9],
            "ticker": ticker,
            "name": "Draft Company",
            "description": "Unpublished",
            "terms": "Proposed IPO",
            "rights": "Declared dividends",
            "quantity": 1000,
        },
    )
    assert created.status_code == 201, created.text
    after = await s[0].get(f"/api/v1/exchange/ticker-availability/{ticker}")
    assert after.status_code == 200 and after.json() == {"available": False}
    assert (await s[0].get(f"/api/v1/exchange/stocks/{ticker}")).status_code == 404


@pytest.mark.asyncio
async def test_revoked_trading_mandate_cancels_resting_order_and_blocks_new_orders(
    api_client, core
):
    s, stock, ticker, buyer = await market_setup(api_client, core)
    resting = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 4, "12.00")
    )
    assert resting.status_code == 201, resting.text
    db = s[1]
    db.execute(
        "UPDATE finance.account_permissions SET revoked_at=now() WHERE account_id=%s AND principal_id=%s AND permission='exchange.trade'",
        (s[10], s[4]),
    )
    db.commit()
    forbidden = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 1, "15.00")
    )
    assert forbidden.status_code == 403, forbidden.text
    s[0].cookies.set("horizon_session", s[7])
    incoming = await command(
        s, "exchange/orders", order_body(ticker, buyer, "BUY", 4, "12.00")
    )
    assert incoming.status_code == 201, incoming.text
    assert incoming.json()["remaining"] == 4
    assert (
        db.execute(
            "SELECT status FROM finance.exchange_orders WHERE id=%s",
            (resting.json()["id"],),
        ).fetchone()[0]
        == "cancelled"
    )
    assert (
        db.execute(
            "SELECT reserved FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
            (stock, s[10]),
        ).fetchone()[0]
        == 0
    )
    db.commit()
    reconciled(db)


async def command(s, path, body, key=None):
    return await s[0].post(
        "/api/v1/" + path,
        headers={**ORIGIN, "Idempotency-Key": key or uuid.uuid4().hex},
        json={"confirmed": True, **body},
    )


async def market_setup(api_client, core, secondary=True):
    s = await setup(api_client, core)
    client, db = s[:2]
    ticker = "X" + uuid.uuid4().hex[:7].upper()
    for permission in ("admin.read", "exchange.manage"):
        authority(db, s[6], permission)
    db.execute(
        "INSERT INTO auth.user_roles(user_id,role_id,granted_by,reason) SELECT %s,id,%s,'Exchange test admin' FROM auth.admin_roles WHERE system_key='superadmin'",
        (s[6], s[6]),
    )
    db.commit()
    issuer = await command(
        s,
        "exchange/issuers",
        {
            "organization_id": s[8],
            "account_id": s[9],
            "ticker": ticker,
            "name": "RP Construction",
            "description": "Virtual construction",
            "terms": "Fixed approved supply",
            "rights": "Ownership and declared dividends only",
            "quantity": 1000,
        },
    )
    assert issuer.status_code == 201, issuer.text
    stock = issuer.json()["id"]
    submitted = await command(s, f"exchange/issuers/{stock}/submit", {})
    assert submitted.status_code == 200, submitted.text
    client.cookies.set("horizon_session", s[7])
    approved = await command(
        s,
        f"admin/exchange/issuers/{stock}/moderate",
        {"status": "approved", "reason": "Review passed"},
    )
    assert approved.status_code == 200, approved.text
    client.cookies.set("horizon_session", s[3])
    ipo = await command(
        s,
        f"exchange/issuers/{stock}/ipo",
        {
            "quantity": 100,
            "price": "10.00",
            "starts_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "ends_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "max_per_buyer": 50,
        },
    )
    assert ipo.status_code == 200, ipo.text
    client.cookies.set("horizon_session", s[5])
    key = uuid.uuid4().hex
    bought = await command(
        s,
        f"exchange/issuers/{stock}/ipo/buy",
        {"account_id": s[10], "quantity": 20},
        key,
    )
    assert bought.status_code == 200, bought.text
    replay = await command(
        s,
        f"exchange/issuers/{stock}/ipo/buy",
        {"account_id": s[10], "quantity": 20},
        key,
    )
    assert replay.json() == bought.json()
    if secondary:
        db.execute(
            "UPDATE finance.exchange_ipos SET ends_at=clock_timestamp()-interval '1 minute' WHERE stock_id=%s",
            (stock,),
        )
        db.commit()
    dest = account(core, s[6])
    fund(core, db, s[6], dest, "500.00")
    return s, stock, ticker, dest


def rpc_command(core, actor, action, data=None, target=""):
    return json.loads(
        invoke(
            core.Exchange,
            pb.BusinessRequest(
                context=context(actor),
                action=action,
                target_id=target,
                payload=json.dumps({**(data or {}), "key": uuid.uuid4().hex}),
            ),
        ).json
    )


def order_body(ticker, account_id, side, qty, price, kind="limit", **extra):
    return {
        "ticker": ticker,
        "account_id": account_id,
        "side": side,
        "kind": kind,
        "quantity": qty,
        "price": price,
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        **extra,
    }


@pytest.mark.asyncio
async def test_ipo_exact_transfer_and_stock_supply_no_rbac_leak(api_client, core):
    s, stock, ticker, _ = await market_setup(api_client, core, False)
    client, db = s[:2]
    assert db.execute(
        "SELECT quantity,reserved FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
        (stock, s[10]),
    ).fetchone() == (20, 0)
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],)
        ).fetchone()[0]
        == "300.00"
    )
    assert (
        db.execute(
            "SELECT count(*) FROM finance.organization_members WHERE organization_id=%s AND principal_id=%s",
            (s[8], s[4]),
        ).fetchone()[0]
        == 0
    )
    assert (await client.get(f"/api/v1/organizations/{s[8]}")).status_code == 403
    assert (
        await command(
            s,
            f"exchange/issuers/{stock}/ipo/buy",
            {"account_id": s[10], "quantity": 31},
        )
    ).status_code == 409
    public = await client.get("/api/v1/exchange/stocks/" + ticker)
    assert public.status_code == 200, public.text
    assert (
        public.json()["last_price"] == "10.00"
        and "treasury_account_id" not in public.json()
    )
    assert (
        db.execute(
            "SELECT sum(quantity) FROM finance.exchange_positions WHERE stock_id=%s",
            (stock,),
        ).fetchone()[0]
        == 1000
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_partial_fill_maker_price_portfolio_and_cancel_reserves(api_client, core):
    s, stock, ticker, buyer = await market_setup(api_client, core)
    client, db = s[:2]
    sell = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 10, "12.00")
    )
    assert sell.status_code == 201, sell.text
    assert (
        db.execute(
            "SELECT reserved FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
            (stock, s[10]),
        ).fetchone()[0]
        == 10
    )
    db.commit()
    client.cookies.set("horizon_session", s[7])
    body = order_body(ticker, buyer, "BUY", 4, "13.00")
    body.pop("expires_at")  # default expiry must not change the retry fingerprint
    key = uuid.uuid4().hex
    bought = await command(s, "exchange/orders", body, key)
    assert bought.status_code == 201, bought.text
    assert (
        bought.json()["status"] == "filled" and bought.json()["reserved_cash"] == "0.00"
    )
    assert (await command(s, "exchange/orders", body, key)).json() == bought.json()
    assert db.execute(
        "SELECT balance::text,held::text FROM finance.accounts WHERE id=%s", (buyer,)
    ).fetchone() == ("452.00", "0.00")
    assert db.execute(
        "SELECT total::text,price::text FROM finance.exchange_trades WHERE buy_order_id=%s",
        (bought.json()["id"],),
    ).fetchone() == ("48.00", "12.00")
    db.commit()
    from test_release_22 import drain

    await drain(client, api_client[2])
    events = db.execute(
        "SELECT payload->>'type' FROM auth.discord_outbox WHERE user_id=%s AND payload->>'operation_id'=(SELECT operation_id::text FROM finance.exchange_trades WHERE buy_order_id=%s)",
        (s[6], bought.json()["id"]),
    ).fetchall()
    assert events == [("exchange.buy",)]
    db.commit()
    portfolio = await client.get(
        "/api/v1/exchange/portfolio", params={"account_id": buyer}
    )
    assert portfolio.status_code == 200, portfolio.text
    assert portfolio.json()["items"][0]["quantity"] == 4
    assert portfolio.json()["items"][0]["average_price"] == "12.00"
    assert (
        await client.get("/api/v1/exchange/portfolio", params={"account_id": s[10]})
    ).status_code == 403
    client.cookies.set("horizon_session", s[5])
    cancelled = await command(s, "exchange/orders/" + sell.json()["id"] + "/cancel", {})
    assert cancelled.status_code == 200, cancelled.text
    assert (
        cancelled.json()["remaining"] == 6 and cancelled.json()["status"] == "cancelled"
    )
    assert db.execute(
        "SELECT quantity,reserved FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
        (stock, s[10]),
    ).fetchone() == (16, 0)
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_buy_reserve_blocks_other_payments_and_cancel_releases(api_client, core):
    from test_core_finance import request

    s, _, ticker, buyer = await market_setup(api_client, core)
    s[0].cookies.set("horizon_session", s[7])
    reserve = await command(
        s, "exchange/orders", order_body(ticker, buyer, "BUY", 40, "10.00")
    )
    assert reserve.status_code == 201, reserve.text
    db = s[1]
    assert (
        db.execute(
            "SELECT held::text FROM finance.accounts WHERE id=%s", (buyer,)
        ).fetchone()[0]
        == "400.00"
    )
    db.commit()
    with pytest.raises(grpc.RpcError):
        invoke(core.Transfer, request(s[6], buyer, s[10], "101.00"))
    assert (
        await command(s, "exchange/orders/" + reserve.json()["id"] + "/cancel", {})
    ).status_code == 200
    assert (
        db.execute(
            "SELECT held::text FROM finance.accounts WHERE id=%s", (buyer,)
        ).fetchone()[0]
        == "0.00"
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_market_budget_price_limit_insufficient_and_self_trade(api_client, core):
    s, _, ticker, buyer = await market_setup(api_client, core)
    client, db = s[:2]
    sell = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 10, "12.00")
    )
    assert sell.status_code == 201, sell.text
    own = await command(
        s, "exchange/orders", order_body(ticker, s[10], "BUY", 1, "12.00")
    )
    assert own.status_code == 409, own.text
    oversell = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 11, "13.00")
    )
    assert oversell.status_code == 409, oversell.text
    client.cookies.set("horizon_session", s[7])
    insufficient = await command(
        s, "exchange/orders", order_body(ticker, buyer, "BUY", 100, "10.00")
    )
    assert insufficient.status_code == 409, insufficient.text
    market = await command(
        s,
        "exchange/orders",
        order_body(ticker, buyer, "BUY", 10, "13.00", "market", budget="30.00"),
    )
    assert market.status_code == 201, market.text
    assert market.json()["status"] == "cancelled" and market.json()["remaining"] == 8
    assert db.execute(
        "SELECT balance::text,held::text FROM finance.accounts WHERE id=%s", (buyer,)
    ).fetchone() == ("476.00", "0.00")
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_price_time_priority_concurrent_buyers_and_suspend(api_client, core):
    s, stock, ticker, buyer = await market_setup(api_client, core)
    first = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 3, "12.00")
    )
    second = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 3, "12.00")
    )
    assert first.status_code == 201 and second.status_code == 201
    db = s[1]
    user, _ = session_cookie(db)
    other = account(core, user)
    fund(core, db, user, other, "500.00")

    def buy(args):
        actor, account_id = args
        return rpc_command(
            core,
            actor,
            "order.create",
            order_body(ticker, account_id, "BUY", 3, "13.00"),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(buy, [(s[6], buyer), (user, other)]))
    assert all(i["status"] == "filled" for i in results)
    deals = db.execute(
        "SELECT sell_order_id::text FROM finance.exchange_trades WHERE sell_order_id IS NOT NULL AND stock_id=%s ORDER BY sequence",
        (stock,),
    ).fetchall()
    assert deals == [(first.json()["id"],), (second.json()["id"],)]
    db.commit()
    s[0].cookies.set("horizon_session", s[7])
    paused = await command(
        s,
        f"admin/exchange/issuers/{stock}/moderate",
        {"status": "suspended", "reason": "Market review"},
    )
    assert paused.status_code == 200, paused.text
    blocked = await command(
        s, "exchange/orders", order_body(ticker, buyer, "BUY", 1, "1.00")
    )
    assert blocked.status_code == 409, blocked.text
    assert (
        db.execute(
            "SELECT sum(quantity) FROM finance.exchange_positions WHERE stock_id=%s",
            (stock,),
        ).fetchone()[0]
        == 1000
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_atomic_dividends_snapshot_rounding_and_replay(api_client, core):
    s, stock, _, _ = await market_setup(api_client, core)
    client, db = s[:2]
    # The organization owner can approve their own fixed record-date snapshot.
    client.cookies.set("horizon_session", s[3])
    member = await client.put(
        f"/api/v1/organizations/{s[8]}/members/{s[6]}",
        headers=ORIGIN,
        json={"user_id": s[6], "role": "administrator"},
    )
    assert member.status_code == 200, member.text
    record = datetime.now(UTC)
    div = await command(
        s,
        "exchange/dividends",
        {
            "stock_id": stock,
            "account_id": s[9],
            "mode": "total",
            "amount": "10.01",
            "record_at": record.isoformat(),
            "pay_at": record.isoformat(),
        },
    )
    assert div.status_code == 201, div.text
    ident = div.json()["id"]
    approval = await command(s, f"exchange/dividends/{ident}/approve", {})
    assert approval.status_code == 200, approval.text
    assert approval.json()["total"] == "10.01" and len(approval.json()["items"]) == 1
    paid = await command(s, f"exchange/dividends/{ident}/execute", {})
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "completed"
    replay = await command(s, f"exchange/dividends/{ident}/execute", {})
    assert (
        replay.status_code == 200
        and replay.json()["items"][0]["operation_id"]
        == paid.json()["items"][0]["operation_id"]
    )
    assert (
        db.execute(
            "SELECT dividends_cents FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
            (stock, s[10]),
        ).fetchone()[0]
        == 1001
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_concurrent_sells_do_not_oversubscribe_and_expiry_unlocks(
    api_client, core
):
    s, stock, ticker, _ = await market_setup(api_client, core)

    def sell(_):
        try:
            return rpc_command(
                core,
                s[4],
                "order.create",
                order_body(ticker, s[10], "SELL", 15, "20.00"),
            )
        except grpc.RpcError as error:
            return error.code()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(sell, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    db = s[1]
    assert (
        db.execute(
            "SELECT reserved FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
            (stock, s[10]),
        ).fetchone()[0]
        == 15
    )
    db.execute(
        "UPDATE finance.exchange_orders SET expires_at=clock_timestamp()-interval '1 second' WHERE stock_id=%s",
        (stock,),
    )
    db.commit()
    cmd = pb.BusinessRequest(context=context(s[4]), action="maintenance", payload="{}")
    cmd.context.source = "exchange-worker"
    invoke(core.Exchange, cmd)
    assert (
        db.execute(
            "SELECT reserved FROM finance.exchange_positions WHERE stock_id=%s AND account_id=%s",
            (stock, s[10]),
        ).fetchone()[0]
        == 0
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_exchange_api_key_opt_in_account_scope_and_no_merchant_trade(
    api_client, core
):
    s, _, ticker, _ = await market_setup(api_client, core)
    client = s[0]
    key = await client.post(
        "/api/v1/api-keys",
        headers=ORIGIN,
        json={
            "name": "Read investments",
            "scopes": ["exchange.read"],
            "account_ids": [s[10]],
        },
    )
    assert key.status_code == 201, key.text
    auth = {"Authorization": "Bearer " + key.json()["secret"]}
    assert (
        await client.get(
            "/api/v1/exchange/portfolio", params={"account_id": s[10]}, headers=auth
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/exchange/orders",
            headers={**auth, "Idempotency-Key": uuid.uuid4().hex},
            json={**order_body(ticker, s[10], "SELL", 1, "10.00"), "confirmed": True},
        )
    ).status_code == 403
    wrong = await client.get(
        "/api/v1/exchange/portfolio", params={"account_id": s[9]}, headers=auth
    )
    assert wrong.status_code == 403
    client.cookies.set("horizon_session", s[3])
    merchant = await client.post(
        "/api/v1/api-keys",
        headers=ORIGIN,
        json={
            "name": "Shop only",
            "scopes": ["payment_requests.read"],
            "organization_id": s[8],
            "account_ids": [s[9]],
        },
    )
    assert merchant.status_code == 201, merchant.text
    assert (
        await client.get(
            "/api/v1/exchange/orders",
            headers={"Authorization": "Bearer " + merchant.json()["secret"]},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_failed_dividend_rolls_back_all_recipients_and_snapshot_is_sealed(
    api_client, core
):
    s, stock, ticker, buyer = await market_setup(api_client, core)
    client, db = s[:2]
    sell = await command(
        s, "exchange/orders", order_body(ticker, s[10], "SELL", 3, "10.00")
    )
    assert sell.status_code == 201
    client.cookies.set("horizon_session", s[7])
    buy = await command(
        s, "exchange/orders", order_body(ticker, buyer, "BUY", 3, "10.00")
    )
    assert buy.status_code == 201, buy.text
    client.cookies.set("horizon_session", s[3])
    assert (
        await client.put(
            f"/api/v1/organizations/{s[8]}/members/{s[6]}",
            headers=ORIGIN,
            json={"user_id": s[6], "role": "administrator"},
        )
    ).status_code == 200
    moment = datetime.now(UTC).isoformat()
    div = await command(
        s,
        "exchange/dividends",
        {
            "stock_id": stock,
            "account_id": s[9],
            "mode": "total",
            "amount": "10.01",
            "record_at": moment,
            "pay_at": moment,
        },
    )
    assert div.status_code == 201, div.text
    ident = div.json()["id"]
    client.cookies.set("horizon_session", s[7])
    approved = await command(s, f"exchange/dividends/{ident}/approve", {})
    assert approved.status_code == 200, approved.text
    assert approved.json()["rounding_remainder"] == "0.01"
    db.execute("UPDATE finance.accounts SET status='frozen' WHERE id=%s", (buyer,))
    db.commit()
    failed = await command(s, f"exchange/dividends/{ident}/execute", {})
    assert failed.status_code == 409, failed.text
    assert (
        db.execute(
            "SELECT count(*) FROM finance.exchange_distributions WHERE dividend_id=%s AND operation_id IS NOT NULL",
            (ident,),
        ).fetchone()[0]
        == 0
    )
    db.commit()
    with pytest.raises(Exception):
        db.execute(
            "UPDATE finance.exchange_distributions SET amount=amount+1 WHERE dividend_id=%s",
            (ident,),
        )
        db.commit()
    db.rollback()
    db.execute("UPDATE finance.accounts SET status='active' WHERE id=%s", (buyer,))
    db.commit()
    done = await command(s, f"exchange/dividends/{ident}/execute", {})
    assert done.status_code == 200, done.text
    assert all(i["status"] == "completed" for i in done.json()["items"])
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_core_crash_rolls_back_partial_matching_and_same_key_recovers(
    api_client, core
):
    import os
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from horizon.banking.v1 import banking_pb2_grpc as rpc
    from test_core_finance import TOKEN

    s, stock, ticker, buyer = await market_setup(api_client, core)
    client, db = s[:2]
    user, _ = session_cookie(db)
    other = account(core, user)
    fund(core, db, user, other, "500.00")
    first = rpc_command(
        core, s[4], "order.create", order_body(ticker, s[10], "SELL", 4, "10.00")
    )
    rpc_command(
        core, user, "order.create", order_body(ticker, other, "BUY", 4, "10.00")
    )
    first = rpc_command(
        core, s[4], "order.create", order_body(ticker, s[10], "SELL", 4, "11.00")
    )
    second = rpc_command(
        core, user, "order.create", order_body(ticker, other, "SELL", 4, "12.00")
    )
    db.commit()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    opts = conninfo_to_dict(os.environ["TEST_FINANCE_DATABASE_URL"])
    opts["application_name"] = "horizon_exchange_crash"
    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "FINANCE_DATABASE_URL": make_conninfo(**opts),
        "GRPC_BIND_ADDRESS": f"127.0.0.1:{port}",
        "INTERNAL_GRPC_TOKEN": TOKEN,
        "HORIZON_INITIAL_ISSUANCE_ACCOUNT_ID": "00000000-0000-0000-0000-000000000001",
    }
    env["PATH"] = (
        str(root / ".tools/vcpkg-installed/x64-windows-release/bin")
        + os.pathsep
        + env.get("PATH", "")
    )
    process = subprocess.Popen(
        [os.environ["CORE_EXECUTABLE"]],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    cmd = pb.BusinessRequest(
        context=context(s[6]),
        action="order.create",
        payload=json.dumps(
            {**order_body(ticker, buyer, "BUY", 8, "12.00"), "key": uuid.uuid4().hex}
        ),
    )
    try:
        grpc.channel_ready_future(channel).result(timeout=20)
        db.execute("SELECT id FROM finance.accounts WHERE id=%s FOR UPDATE", (other,))
        pending = pool.submit(invoke, rpc.BankingServiceStub(channel).Exchange, cmd)
        with psycopg.connect(
            os.environ["TEST_DATABASE_URL"], autocommit=True
        ) as observer:
            waiting = False
            for _ in range(250):
                waiting = observer.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='horizon_exchange_crash' AND wait_event_type='Lock')"
                ).fetchone()[0]
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting, (
                "matching core never reached the deliberately blocked second settlement"
            )
        process.kill()
        process.wait(timeout=10)
        db.rollback()
        with pytest.raises(grpc.RpcError):
            pending.result(timeout=10)
    finally:
        db.rollback()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        channel.close()
        pool.shutdown(wait=True)
    assert db.execute(
        "SELECT balance::text,held::text FROM finance.accounts WHERE id=%s", (buyer,)
    ).fetchone() == ("500.00", "0.00")
    assert db.execute(
        "SELECT remaining FROM finance.exchange_orders WHERE id IN (%s,%s) ORDER BY sequence",
        (first["id"], second["id"]),
    ).fetchall() == [(4,), (4,)]
    db.commit()
    recovered = json.loads(invoke(core.Exchange, cmd).json)
    assert recovered["status"] == "filled"
    assert json.loads(invoke(core.Exchange, cmd).json)["id"] == recovered["id"]
    assert (
        db.execute(
            "SELECT count(*) FROM finance.exchange_trades WHERE buy_order_id=%s",
            (recovered["id"],),
        ).fetchone()[0]
        == 2
    )
    db.commit()
    reconciled(db)
