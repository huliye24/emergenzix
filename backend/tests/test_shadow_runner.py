from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.shadow_runner import (
    ShadowRunnerError,
    create_shadow_session,
    record_shadow_cycle_failure,
    get_shadow_session,
    run_shadow_cycle,
)
from emergenzix_api.shadow_worker import run_active_sessions_once
from emergenzix_api.shadow_api import ShadowSessionCreate


def _seed_source_backtest(db: Database) -> tuple[str, str]:
    with db.connection() as connection:
        version = connection.execute(
            """
            SELECT v.id, v.strategy_id
            FROM strategy_versions AS v
            JOIN strategies AS s ON s.id = v.strategy_id
            WHERE s.symbol = 'BTCUSDT' AND s.strategy_type = 'turtle_breakout'
              AND v.slow_window = 20 AND v.fast_window = 10
            """
        ).fetchone()
        backtest_id = "selected-good-backtest"
        connection.execute(
            """
            INSERT INTO backtests
                (id, strategy_id, strategy_version_id, symbol, bars_count,
                 initial_cash, final_equity, total_return_pct, max_drawdown_pct,
                 number_of_trades, win_rate_pct, benchmark_return_pct,
                 result_json, created_at)
            VALUES (?, ?, ?, 'BTCUSDT', 200, 10000, 11000, 10, 5, 3, 66, 8, ?, ?)
            """,
            (
                backtest_id,
                version["strategy_id"],
                version["id"],
                json.dumps({"id": backtest_id}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return backtest_id, version["id"]


def _payload(start: datetime, closes: list[float], *, timeframe: str = "1h") -> dict:
    bars = []
    for index, close in enumerate(closes):
        opening = closes[index - 1] if index else close
        bars.append(
            {
                "timestamp": (start + timedelta(hours=index)).isoformat(),
                "open": opening,
                "high": max(opening, close) + 1,
                "low": min(opening, close) - 1,
                "close": close,
                "volume": 100 + index,
            }
        )
    return {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": timeframe,
        "bars": bars,
    }


@pytest.fixture
def shadow(tmp_path):
    db = Database(str(tmp_path / "shadow.db"), seed_builtin_strategies=True)
    backtest_id, version_id = _seed_source_backtest(db)
    session = create_shadow_session(
        db,
        source_backtest_id=backtest_id,
        strategy_version_id=version_id,
        symbol="BTCUSDT",
        timeframe="1h",
        initial_cash=10_000,
    )
    return db, session


def test_completed_bar_signal_is_idempotent_and_fills_at_next_open(shadow):
    db, session = shadow
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    first = _payload(start, [100.0] * 20 + [105.0, 106.0])
    now = start + timedelta(hours=21, minutes=30)

    decision = run_shadow_cycle(
        db, session_id=session["id"], market_payload=first, now=now
    )
    duplicate = run_shadow_cycle(
        db, session_id=session["id"], market_payload=first, now=now
    )
    assert decision["signal"] == "ENTER"
    assert decision["fill_side"] is None
    assert duplicate["id"] == decision["id"]

    second = _payload(start, [100.0] * 20 + [105.0, 106.0, 106.0])
    filled = run_shadow_cycle(
        db,
        session_id=session["id"],
        market_payload=second,
        now=start + timedelta(hours=22, minutes=30),
    )
    assert filled["fill_side"] == "BUY"
    assert filled["fill_price"] == pytest.approx(105.0 * 1.0005)
    assert filled["position_quantity"] > 0
    state = get_shadow_session(db, session["id"])
    assert len(state["decisions"]) == 2
    assert state["quantity"] == pytest.approx(filled["position_quantity"])
    assert state["cash"] < state["initial_cash"]


def test_stale_or_mismatched_data_fails_without_a_decision(shadow):
    db, session = shadow
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = _payload(start, [100.0] * 20 + [105.0, 106.0])
    with pytest.raises(ShadowRunnerError, match="stale"):
        run_shadow_cycle(
            db,
            session_id=session["id"],
            market_payload=payload,
            now=start + timedelta(days=1),
        )
    payload["symbol"] = "ETH/USDT"
    with pytest.raises(ShadowRunnerError, match="does not match"):
        run_shadow_cycle(
            db,
            session_id=session["id"],
            market_payload=payload,
            now=start + timedelta(hours=21),
        )
    assert get_shadow_session(db, session["id"])["decisions"] == []


def test_worker_polls_active_sessions_and_records_equity(shadow):
    db, session = shadow
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    payload = _payload(start, [100.0] * 20 + [105.0, 106.0])

    outcomes = run_active_sessions_once(
        db,
        fetcher=lambda **_: payload,
        now=start + timedelta(hours=21, minutes=30),
    )

    assert outcomes == [
        {
            "session_id": session["id"],
            "completed_at": (start + timedelta(hours=20)).isoformat(),
            "signal": "ENTER",
            "fill_side": None,
            "equity": 10_000.0,
        }
    ]


def test_worker_queues_only_selected_session_and_delivery_failure_is_isolated(
    shadow, tmp_path
):
    db, session = shadow
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    payload = _payload(start, [100.0] * 20 + [105.0, 106.0])
    outbox = NotificationOutbox(tmp_path / "follow-outbox.sqlite3")

    unselected = run_active_sessions_once(
        db,
        fetcher=lambda **_: payload,
        now=start + timedelta(hours=21, minutes=30),
        notification_outbox=outbox,
        notification_session_id="another-session",
    )
    selected = run_active_sessions_once(
        db,
        fetcher=lambda **_: payload,
        now=start + timedelta(hours=21, minutes=30),
        notification_outbox=outbox,
        notification_session_id=session["id"],
    )

    assert "notification_ids" not in unselected[0]
    assert len(selected[0]["notification_ids"]) == 1
    assert outbox.pending_count() == 1
    state = get_shadow_session(db, session["id"])
    assert len(state["decisions"]) == 1


def test_worker_records_one_immutable_sanitized_failure_per_expected_bar(shadow):
    db, session = shadow
    observed_at = datetime(2026, 11, 2, 5, 30, tzinfo=timezone.utc)
    state_before = get_shadow_session(db, session["id"])

    def unavailable(**_):
        raise RuntimeError("secret-bearing provider detail must not be persisted")

    first = run_active_sessions_once(db, fetcher=unavailable, now=observed_at)
    duplicate = run_active_sessions_once(db, fetcher=unavailable, now=observed_at)

    assert first == duplicate
    assert first[0]["error_code"] == "UNEXPECTED_WORKER_ERROR"
    assert first[0]["expected_completed_at"] == "2026-11-02T04:00:00+00:00"
    assert "secret" not in json.dumps(first)
    state_after = get_shadow_session(db, session["id"])
    assert state_after["cash"] == state_before["cash"]
    assert state_after["quantity"] == state_before["quantity"]
    assert state_after["decisions"] == []
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM shadow_cycle_failures WHERE session_id = ?",
            (session["id"],),
        ).fetchall()
        assert len(rows) == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE shadow_cycle_failures SET error_code = 'INVALID_MARKET_DATA'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM shadow_cycle_failures")


def test_success_can_follow_failure_without_erasing_failure_evidence(shadow):
    db, session = shadow
    observed_at = datetime(2026, 11, 1, 21, 30, tzinfo=timezone.utc)
    failure = record_shadow_cycle_failure(
        db,
        session_id=session["id"],
        timeframe="1h",
        observed_at=observed_at,
        error_code="MARKET_DATA_UNAVAILABLE",
    )
    payload = _payload(
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        [100.0] * 20 + [105.0, 106.0],
    )

    decision = run_shadow_cycle(
        db,
        session_id=session["id"],
        market_payload=payload,
        now=observed_at,
    )

    assert failure["expected_completed_at"] == decision["completed_at"]
    with db.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadow_cycle_failures WHERE session_id = ?",
            (session["id"],),
        ).fetchone()[0] == 1


def test_session_requires_exact_backtest_version(tmp_path):
    db = Database(str(tmp_path / "mismatch.db"), seed_builtin_strategies=True)
    backtest_id, _ = _seed_source_backtest(db)
    with db.connection() as connection:
        other = connection.execute(
            """
            SELECT v.id FROM strategy_versions AS v
            JOIN strategies AS s ON s.id = v.strategy_id
            WHERE s.symbol = 'BTCUSDT' AND v.slow_window = 55
            """
        ).fetchone()["id"]
    with pytest.raises(ShadowRunnerError, match="does not match"):
        create_shadow_session(
            db,
            source_backtest_id=backtest_id,
            strategy_version_id=other,
            symbol="BTCUSDT",
            timeframe="1h",
            initial_cash=10_000,
        )


def test_sol_turtle_versions_and_shadow_request_are_available(tmp_path):
    db = Database(str(tmp_path / "sol.db"), seed_builtin_strategies=True)
    with db.connection() as connection:
        versions = connection.execute(
            """
            SELECT v.slow_window, v.fast_window
            FROM strategy_versions AS v
            JOIN strategies AS s ON s.id = v.strategy_id
            WHERE s.symbol = 'SOLUSDT' AND s.strategy_type = 'turtle_breakout'
            ORDER BY v.slow_window
            """
        ).fetchall()
    assert [(row["slow_window"], row["fast_window"]) for row in versions] == [
        (20, 10),
        (55, 20),
    ]
    request = ShadowSessionCreate.model_validate(
        {
            "source_backtest_id": "backtest-id",
            "strategy_version_id": "version-id",
            "symbol": "SOLUSDT",
            "timeframe": "4h",
            "initial_cash": 10_000,
        }
    )
    assert request.symbol == "SOLUSDT"
