from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from emergenzix_api.database import Database
from emergenzix_api.short_shadow_api import BtcShortFleetCreate, create_fleet
from emergenzix_api.short_shadow_runner import (
    STRATEGIES,
    ShortShadowRunnerError,
    create_btc_short_fleet,
    get_short_shadow_session,
    run_short_shadow_cycle,
)
from emergenzix_api.shadow_worker import run_active_short_sessions_once


def _payload(start: datetime, closes: list[float]) -> dict:
    bars = []
    for index, close in enumerate(closes):
        opening = closes[index - 1] if index else close
        bars.append(
            {
                "timestamp": (start + timedelta(hours=index)).isoformat(),
                "open": opening,
                "high": max(opening, close) + 1.0,
                "low": min(opening, close) - 1.0,
                "close": close,
                "volume": 100.0 + index,
            }
        )
    return {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "bars": bars,
    }


def _entry_closes(strategy_key: str) -> list[float]:
    if strategy_key == "ema_bear_trend_20_50":
        return [130.0 - 0.5 * index for index in range(52)] + [104.0]
    return [100.0] * 51 + [90.0, 90.0]


@pytest.fixture
def short_fleet(tmp_path):
    db = Database(str(tmp_path / "short-shadow.db"))
    fleet = create_btc_short_fleet(db)
    return db, fleet


def test_fleet_is_three_independent_paper_only_short_accounts(short_fleet):
    db, fleet = short_fleet
    assert fleet["execution_mode"] == "PAPER_ONLY"
    assert fleet["live_order_permission"] is False
    assert len(fleet["sessions"]) == 3
    assert {row["strategy_key"] for row in fleet["sessions"]} == set(STRATEGIES)
    assert {row["initial_cash"] for row in fleet["sessions"]} == {10_000.0}
    with db.connection() as connection:
        session_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'short_shadow_sessions'"
        ).fetchone()["sql"]
        decision_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'short_shadow_decisions'"
        ).fetchone()["sql"]
    assert "'SHORT', 'COVER'" in session_sql
    assert "'HOLD', 'SHORT', 'COVER'" in decision_sql
    assert "BUY" not in session_sql + decision_sql
    assert "LONG" not in session_sql + decision_sql


@pytest.mark.parametrize("strategy_key", sorted(STRATEGIES))
def test_each_strategy_signals_then_fills_short_at_next_open(short_fleet, strategy_key):
    db, fleet = short_fleet
    session = next(row for row in fleet["sessions"] if row["strategy_key"] == strategy_key)
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    closes = _entry_closes(strategy_key)
    first = run_short_shadow_cycle(
        db,
        session_id=session["id"],
        market_payload=_payload(start, closes),
        now=start + timedelta(hours=51, minutes=30),
    )
    duplicate = run_short_shadow_cycle(
        db,
        session_id=session["id"],
        market_payload=_payload(start, closes),
        now=start + timedelta(hours=51, minutes=30),
    )
    assert first["signal"] == "SHORT"
    assert first["fill_side"] is None
    assert duplicate["id"] == first["id"]

    second_closes = closes + [closes[-1] - 1.0]
    filled = run_short_shadow_cycle(
        db,
        session_id=session["id"],
        market_payload=_payload(start, second_closes),
        now=start + timedelta(hours=52, minutes=30),
    )
    assert filled["fill_side"] == "SHORT"
    assert filled["fill_quantity"] > 0
    assert filled["short_quantity"] > 0
    assert filled["fill_quantity"] * filled["fill_price"] <= 10_000.0
    state = get_short_shadow_session(db, session["id"])
    assert state["live_order_permission"] is False
    assert {item["signal"] for item in state["decisions"]} <= {"HOLD", "SHORT", "COVER"}


def test_mismatched_or_stale_market_data_fails_closed(short_fleet):
    db, fleet = short_fleet
    session = fleet["sessions"][0]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = _payload(start, [100.0] * 53)
    payload["symbol"] = "ETH/USDT"
    with pytest.raises(ShortShadowRunnerError, match="does not match"):
        run_short_shadow_cycle(
            db, session_id=session["id"], market_payload=payload,
            now=start + timedelta(hours=51, minutes=30),
        )
    payload["symbol"] = "BTC/USDT"
    with pytest.raises(ShortShadowRunnerError, match="stale"):
        run_short_shadow_cycle(
            db, session_id=session["id"], market_payload=payload,
            now=start + timedelta(days=10),
        )
    assert get_short_shadow_session(db, session["id"])["decisions"] == []


def test_api_creates_only_paper_fleet(tmp_path):
    db = Database(str(tmp_path / "api.db"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=db)))
    body = create_fleet(BtcShortFleetCreate(), request)
    assert body["live_order_permission"] is False
    assert len(body["sessions"]) == 3


def test_worker_polls_all_three_short_sessions(short_fleet):
    db, fleet = short_fleet
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def fetcher(**_):
        return _payload(start, [100.0] * 51 + [90.0, 90.0])

    outcomes = run_active_short_sessions_once(
        db, fetcher=fetcher, now=start + timedelta(hours=51, minutes=30)
    )
    assert len(outcomes) == 3
    assert {item["strategy_key"] for item in outcomes} == set(STRATEGIES)
    assert {item["execution_mode"] for item in outcomes} == {"PAPER_ONLY"}
    assert all("error" not in item for item in outcomes)
