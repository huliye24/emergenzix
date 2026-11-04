from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from emergenzix_api.learning_loop import (
    LearningLoopStatusError,
    get_learning_loop_status,
)
from emergenzix_api.main import create_app
from emergenzix_api.shadow_runner import (
    create_shadow_session,
    record_shadow_cycle_failure,
)


def _seed_selected_session(app, *, suffix: str = "") -> dict:
    db = app.state.database
    with db.connection() as connection:
        version = connection.execute(
            """
            SELECT v.id, v.strategy_id
            FROM strategy_versions AS v
            JOIN strategies AS s ON s.id = v.strategy_id
            WHERE s.symbol = 'BTCUSDT'
              AND s.strategy_type = 'turtle_breakout'
              AND v.slow_window = 20 AND v.fast_window = 10
            """
        ).fetchone()
        backtest_id = f"learning-loop-backtest{suffix}"
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
                json.dumps(
                    {
                        "assumptions": {
                            "fee_bps_per_side": 10.0,
                            "slippage_bps_per_side": 5.0,
                        }
                    }
                ),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return create_shadow_session(
        db,
        source_backtest_id=backtest_id,
        strategy_version_id=version["id"],
        symbol="BTCUSDT",
        timeframe="4h",
        initial_cash=10_000,
    )


def test_status_answers_five_questions_and_keeps_gate_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("EMERGENZIX_SEED_BUILTIN_STRATEGIES", "true")
    app = create_app(db_path=tmp_path / "learning-loop.db")
    selected = _seed_selected_session(app)

    status = get_learning_loop_status(app.state.database)
    assert any(
        route.path == "/api/v1/learning-loop/status" for route in app.routes
    )
    assert status["execution_mode"] == "PAPER_ONLY"
    assert status["live_order_permission"] is False
    assert status["strategy"]["session_id"] == selected["id"]
    assert status["strategy"]["symbol"] == "BTCUSDT"
    assert status["strategy"]["timeframe"] == "4h"
    assert status["latest_decision"] is None
    assert status["portfolio"]["marked_equity"] == 10_000
    assert status["portfolio"]["open_risk_status"] == "NOT_IMPLEMENTED"
    assert status["backtest_deviation"]["cost_assumptions_match"] is True
    assert status["observation_gate"]["independent_decision_count"] == 0
    assert status["observation_gate"]["failure_recording_status"] == "IMPLEMENTED"
    assert status["observation_gate"]["evaluation_permitted"] is False


def test_failure_counts_as_candle_evidence_but_not_as_a_decision(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EMERGENZIX_SEED_BUILTIN_STRATEGIES", "true")
    app = create_app(db_path=tmp_path / "failure-evidence.db")
    selected = _seed_selected_session(app)
    record_shadow_cycle_failure(
        app.state.database,
        session_id=selected["id"],
        timeframe="4h",
        observed_at=datetime.now(timezone.utc),
        error_code="MARKET_DATA_UNAVAILABLE",
    )

    status = get_learning_loop_status(app.state.database)

    assert status["latest_failure"]["error_code"] == "MARKET_DATA_UNAVAILABLE"
    assert status["observation_gate"]["failure_count"] == 1
    assert status["observation_gate"]["covered_expected_candle_count"] == 1
    assert status["observation_gate"]["independent_decision_count"] == 0
    assert status["observation_gate"]["expected_candle_coverage_verified"] is True
    assert status["observation_gate"]["evaluation_permitted"] is False


def test_status_fails_closed_without_one_unambiguous_session(tmp_path, monkeypatch):
    monkeypatch.setenv("EMERGENZIX_SEED_BUILTIN_STRATEGIES", "true")
    app = create_app(db_path=tmp_path / "ambiguous.db")
    with pytest.raises(LearningLoopStatusError, match="no active"):
        get_learning_loop_status(app.state.database)

    _seed_selected_session(app, suffix="-one")
    _seed_selected_session(app, suffix="-two")
    with pytest.raises(LearningLoopStatusError, match="multiple active"):
        get_learning_loop_status(app.state.database)
