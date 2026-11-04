"""Read-only status for the first-strategy PAPER learning loop."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .database import Database
from .shadow_runner import TIMEFRAME_SECONDS, expected_completed_at


class LearningLoopStatusError(ValueError):
    """The selected learning loop is missing or ambiguous."""


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_learning_loop_status(db: Database) -> dict[str, Any]:
    """Return the five-question status without changing runtime state."""
    with db.connection() as connection:
        sessions = connection.execute(
            """
            SELECT ss.*, s.name AS strategy_name, v.version AS strategy_version,
                   v.fast_window, v.slow_window, v.parameters_json,
                   b.initial_cash AS backtest_initial_cash,
                   b.final_equity AS backtest_final_equity,
                   b.total_return_pct AS backtest_return_pct,
                   b.max_drawdown_pct AS backtest_max_drawdown_pct,
                   b.number_of_trades AS backtest_trade_count,
                   b.result_json AS backtest_result_json
            FROM shadow_sessions AS ss
            JOIN strategies AS s ON s.id = ss.strategy_id
            JOIN strategy_versions AS v ON v.id = ss.strategy_version_id
            JOIN backtests AS b ON b.id = ss.source_backtest_id
            WHERE ss.status = 'ACTIVE'
              AND ss.symbol = 'BTCUSDT'
              AND ss.timeframe = '4h'
              AND v.strategy_type = 'turtle_breakout'
              AND v.slow_window = 20
              AND v.fast_window = 10
            """
        ).fetchall()
        if not sessions:
            raise LearningLoopStatusError(
                "no active BTCUSDT 4h Turtle 20/10 learning-loop session"
            )
        if len(sessions) != 1:
            raise LearningLoopStatusError(
                "multiple active BTCUSDT 4h Turtle 20/10 sessions; selection is ambiguous"
            )
        session = sessions[0]
        latest = connection.execute(
            """
            SELECT * FROM shadow_decisions
            WHERE session_id = ?
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            (session["id"],),
        ).fetchone()
        counts = connection.execute(
            """
            SELECT COUNT(*) AS decision_count,
                   COUNT(DISTINCT completed_at) AS independent_decision_count,
                   MIN(completed_at) AS first_completed_at,
                   MAX(completed_at) AS latest_completed_at,
                   SUM(CASE WHEN fill_side IS NOT NULL THEN 1 ELSE 0 END) AS fill_count,
                   COALESCE(SUM(fee), 0) AS total_fees
            FROM shadow_decisions
            WHERE session_id = ?
            """,
            (session["id"],),
        ).fetchone()
        failures = connection.execute(
            """
            SELECT * FROM shadow_cycle_failures
            WHERE session_id = ?
            ORDER BY expected_completed_at DESC
            """,
            (session["id"],),
        ).fetchall()
        evidence_rows = connection.execute(
            """
            SELECT completed_at AS evidence_at FROM shadow_decisions
            WHERE session_id = ?
            UNION
            SELECT expected_completed_at AS evidence_at FROM shadow_cycle_failures
            WHERE session_id = ?
            ORDER BY evidence_at
            """,
            (session["id"], session["id"]),
        ).fetchall()

    latest_close = float(latest["close_price"]) if latest else 0.0
    equity = (
        float(latest["equity"])
        if latest
        else float(session["cash"]) + float(session["quantity"]) * latest_close
    )
    open_notional = float(session["quantity"]) * latest_close
    exposure_pct = open_notional / equity * 100.0 if equity > 0 else 0.0
    paper_return_pct = (
        (equity / float(session["initial_cash"]) - 1.0) * 100.0
        if float(session["initial_cash"]) > 0
        else 0.0
    )

    evidence_times = [row["evidence_at"] for row in evidence_rows]
    first_at = evidence_times[0] if evidence_times else None
    latest_expected_at = expected_completed_at(
        timeframe=session["timeframe"], observed_at=datetime.now(timezone.utc)
    )
    latest_at = evidence_times[-1] if evidence_times else None
    observation_days = (
        (_iso(latest_expected_at) - _iso(first_at)).total_seconds() / 86_400.0
        if first_at
        else 0.0
    )
    independent_count = int(counts["independent_decision_count"])
    expected_count = 0
    if first_at and _iso(first_at) <= _iso(latest_expected_at):
        expected_count = int(
            (_iso(latest_expected_at) - _iso(first_at)).total_seconds()
            // TIMEFRAME_SECONDS[session["timeframe"]]
        ) + 1
    covered_count = len(evidence_times)

    backtest_result = json.loads(session["backtest_result_json"])
    assumptions = backtest_result.get("assumptions", {})
    cost_match = (
        assumptions.get("fee_bps_per_side") == float(session["fee_bps"])
        and assumptions.get("slippage_bps_per_side")
        == float(session["slippage_bps"])
    )
    coverage_verified = expected_count > 0 and covered_count == expected_count
    reconciliation_verified = False
    gate_open = (
        observation_days >= 30
        and independent_count >= 180
        and coverage_verified
        and reconciliation_verified
    )

    latest_decision = None
    if latest:
        latest_decision = {
            "completed_at": latest["completed_at"],
            "signal": latest["signal"],
            "reason": latest["reason"],
            "fill_side": latest["fill_side"],
            "input_sha256": latest["input_sha256"],
        }
    latest_failure = None
    if failures:
        latest_failure = {
            "expected_completed_at": failures[0]["expected_completed_at"],
            "error_code": failures[0]["error_code"],
            "created_at": failures[0]["created_at"],
        }

    return {
        "execution_mode": "PAPER_ONLY",
        "live_order_permission": False,
        "strategy": {
            "session_id": session["id"],
            "strategy_id": session["strategy_id"],
            "strategy_name": session["strategy_name"],
            "strategy_version_id": session["strategy_version_id"],
            "strategy_version": session["strategy_version"],
            "parameters_sha256": session["strategy_sha256"],
            "parameters": json.loads(session["parameters_json"]),
            "symbol": session["symbol"],
            "timeframe": session["timeframe"],
            "source_backtest_id": session["source_backtest_id"],
        },
        "latest_decision": latest_decision,
        "latest_failure": latest_failure,
        "portfolio": {
            "cash": float(session["cash"]),
            "position_quantity": float(session["quantity"]),
            "average_entry_price": float(session["average_entry_price"]),
            "marked_equity": equity,
            "open_notional": open_notional,
            "gross_exposure_pct": exposure_pct,
            "open_risk_status": "NOT_IMPLEMENTED",
        },
        "backtest_deviation": {
            "status": "PARTIAL",
            "cost_assumptions_match": cost_match,
            "paper_return_pct": paper_return_pct,
            "backtest_return_pct": float(session["backtest_return_pct"]),
            "return_difference_pct_points": paper_return_pct
            - float(session["backtest_return_pct"]),
            "paper_fill_count": int(counts["fill_count"] or 0),
            "backtest_trade_count": int(session["backtest_trade_count"]),
            "paper_total_fees": float(counts["total_fees"]),
            "backtest_max_drawdown_pct": float(
                session["backtest_max_drawdown_pct"]
            ),
            "limitation": (
                "Decision-by-decision replay comparison and PAPER drawdown "
                "attribution are not yet implemented."
            ),
        },
        "observation_gate": {
            "target_calendar_days": 30,
            "target_independent_decisions": 180,
            "observation_days_since_first_evidence": observation_days,
            "decision_count": int(counts["decision_count"]),
            "independent_decision_count": independent_count,
            "failure_count": len(failures),
            "covered_expected_candle_count": covered_count,
            "expected_candle_count_since_first_evidence": expected_count,
            "first_completed_at": first_at,
            "latest_completed_at": latest_at,
            "latest_expected_completed_at": latest_expected_at,
            "expected_candle_coverage_verified": coverage_verified,
            "account_reconciliation_verified": reconciliation_verified,
            "failure_recording_status": "IMPLEMENTED",
            "evaluation_permitted": gate_open,
        },
    }
