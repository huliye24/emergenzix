"""Auditable, PAPER-only shadow execution for pinned Turtle versions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .database import Database
from .schemas import Bar
from .watch_agent import TIMEFRAME_SECONDS, _parse_completed_bars


class ShadowRunnerError(ValueError):
    """A fail-closed shadow configuration or market-data error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expected_completed_at(*, timeframe: str, observed_at: datetime) -> str:
    """Return the open timestamp of the latest candle expected to be complete."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise ShadowRunnerError("unsupported shadow timeframe")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ShadowRunnerError("shadow observation time must include a timezone")
    seconds = TIMEFRAME_SECONDS[timeframe]
    boundary = (int(observed_at.timestamp()) // seconds - 1) * seconds
    return datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat()


def record_shadow_cycle_failure(
    db: Database,
    *,
    session_id: str,
    timeframe: str,
    observed_at: datetime,
    error_code: str,
) -> dict[str, Any]:
    """Persist one immutable, sanitized failure per expected completed candle."""
    allowed = {
        "MARKET_DATA_UNAVAILABLE",
        "INVALID_MARKET_DATA",
        "SHADOW_RUNNER_ERROR",
        "UNEXPECTED_WORKER_ERROR",
    }
    if error_code not in allowed:
        raise ShadowRunnerError("unsupported shadow failure code")
    expected_at = expected_completed_at(
        timeframe=timeframe, observed_at=observed_at
    )
    failure_id = str(uuid.uuid4())
    created_at = _now()
    with db.connection() as connection:
        connection.execute(
            """
            INSERT INTO shadow_cycle_failures
                (id, session_id, expected_completed_at, error_code, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, expected_completed_at) DO NOTHING
            """,
            (failure_id, session_id, expected_at, error_code, created_at),
        )
        result = connection.execute(
            """
            SELECT * FROM shadow_cycle_failures
            WHERE session_id = ? AND expected_completed_at = ?
            """,
            (session_id, expected_at),
        ).fetchone()
    if result is None:
        raise ShadowRunnerError("shadow failure record could not be persisted")
    return _row(result)


def _symbol_key(value: str) -> str:
    return value.strip().upper().replace("/", "").replace("-", "").replace("_", "")


def _row(row: Any) -> dict[str, Any]:
    return dict(row)


def create_shadow_session(
    db: Database,
    *,
    source_backtest_id: str,
    strategy_version_id: str,
    symbol: str,
    timeframe: str,
    initial_cash: float,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    """Create an isolated shadow portfolio from an explicitly selected backtest."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise ShadowRunnerError("unsupported shadow timeframe")
    if initial_cash <= 0 or fee_bps < 0 or slippage_bps < 0:
        raise ShadowRunnerError("cash must be positive and costs cannot be negative")
    normalized_symbol = _symbol_key(symbol)
    with db.connection() as connection:
        source = connection.execute(
            """
            SELECT b.id AS backtest_id, b.strategy_id AS backtest_strategy_id,
                   b.strategy_version_id AS backtest_version_id, b.symbol AS backtest_symbol,
                   s.id AS strategy_id, s.symbol AS strategy_symbol,
                   v.id AS strategy_version_id, v.strategy_type, v.parameters_sha256
            FROM backtests AS b
            JOIN strategy_versions AS v ON v.id = ?
            JOIN strategies AS s ON s.id = v.strategy_id
            WHERE b.id = ?
            """,
            (strategy_version_id, source_backtest_id),
        ).fetchone()
        if source is None:
            raise ShadowRunnerError("source backtest or strategy version was not found")
        if source["backtest_version_id"] is None:
            raise ShadowRunnerError("legacy backtests without a pinned version cannot be promoted")
        if (
            source["backtest_strategy_id"] != source["strategy_id"]
            or source["backtest_version_id"] != source["strategy_version_id"]
        ):
            raise ShadowRunnerError("source backtest does not match the selected strategy version")
        if source["strategy_type"] != "turtle_breakout":
            raise ShadowRunnerError("only pinned Turtle versions are supported by this runner")
        if (
            _symbol_key(source["strategy_symbol"]) != normalized_symbol
            or _symbol_key(source["backtest_symbol"]) != normalized_symbol
        ):
            raise ShadowRunnerError("shadow symbol must match the strategy and source backtest")

        session_id = str(uuid.uuid4())
        created_at = _now()
        connection.execute(
            """
            INSERT INTO shadow_sessions
                (id, source_backtest_id, strategy_id, strategy_version_id,
                 strategy_sha256, symbol, timeframe, initial_cash, fee_bps,
                 slippage_bps, cash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                source_backtest_id,
                source["strategy_id"],
                strategy_version_id,
                source["parameters_sha256"],
                normalized_symbol,
                timeframe,
                initial_cash,
                fee_bps,
                slippage_bps,
                initial_cash,
                created_at,
                created_at,
            ),
        )
        result = connection.execute(
            "SELECT * FROM shadow_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return _row(result)


def list_shadow_sessions(db: Database) -> list[dict[str, Any]]:
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM shadow_sessions ORDER BY created_at DESC"
        ).fetchall()
    return [_row(item) for item in rows]


def get_shadow_session(db: Database, session_id: str) -> dict[str, Any] | None:
    with db.connection() as connection:
        session = connection.execute(
            "SELECT * FROM shadow_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return None
        decisions = connection.execute(
            "SELECT * FROM shadow_decisions WHERE session_id = ? ORDER BY completed_at DESC LIMIT 100",
            (session_id,),
        ).fetchall()
    result = _row(session)
    result["decisions"] = [_row(item) for item in decisions]
    return result


def _atr(bars: list[Bar], window: int = 20) -> float:
    ranges: list[float] = []
    for index in range(len(bars) - window, len(bars)):
        bar = bars[index]
        previous_close = float(bars[index - 1].close) if index else float(bar.open)
        ranges.append(
            max(
                float(bar.high) - float(bar.low),
                abs(float(bar.high) - previous_close),
                abs(float(bar.low) - previous_close),
            )
        )
    return sum(ranges) / window


def run_shadow_cycle(
    db: Database,
    *,
    session_id: str,
    market_payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Consume one market snapshot and persist exactly one decision per closed bar."""
    completed = _parse_completed_bars(market_payload)
    current = completed[-1]
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ShadowRunnerError("shadow observation time must include a timezone")

    with db.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            """
            SELECT ss.*, v.strategy_type, v.fast_window, v.slow_window,
                   v.parameters_sha256 AS current_strategy_sha256
            FROM shadow_sessions AS ss
            JOIN strategy_versions AS v ON v.id = ss.strategy_version_id
            WHERE ss.id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            raise ShadowRunnerError("shadow session was not found")
        if session["status"] != "ACTIVE":
            raise ShadowRunnerError("shadow session is paused")
        if session["strategy_sha256"] != session["current_strategy_sha256"]:
            raise ShadowRunnerError("pinned strategy fingerprint no longer matches")
        if session["strategy_type"] != "turtle_breakout":
            raise ShadowRunnerError("unsupported shadow strategy type")
        if (
            _symbol_key(str(market_payload.get("symbol", ""))) != session["symbol"]
            or market_payload.get("timeframe") != session["timeframe"]
        ):
            raise ShadowRunnerError("market payload does not match the shadow session")

        completed_at = current.timestamp.isoformat()
        existing = connection.execute(
            "SELECT * FROM shadow_decisions WHERE session_id = ? AND completed_at = ?",
            (session_id, completed_at),
        ).fetchone()
        if existing is not None:
            return _row(existing)

        age = (observed_at - current.timestamp.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > TIMEFRAME_SECONDS[session["timeframe"]] * 3:
            raise ShadowRunnerError("latest completed candle is missing, stale, or in the future")

        entry_window = int(session["slow_window"])
        exit_window = int(session["fast_window"])
        warmup = max(entry_window, exit_window, 20)
        if len(completed) < warmup + 1:
            raise ShadowRunnerError(f"at least {warmup + 1} completed candles are required")

        cash = float(session["cash"])
        quantity = float(session["quantity"])
        average_entry = float(session["average_entry_price"])
        units = int(session["units"])
        last_add_price = float(session["last_add_price"])
        fee_rate = float(session["fee_bps"]) / 10_000.0
        slippage_rate = float(session["slippage_bps"]) / 10_000.0
        fill_side: str | None = None
        fill_quantity = 0.0
        fill_price: float | None = None
        fee = 0.0

        pending = session["pending_action"]
        if pending in {"ENTER", "ADD"} and session["pending_atr"]:
            fill_price = float(current.open) * (1.0 + slippage_rate)
            marked_equity = cash + quantity * float(completed[-2].close)
            risk_quantity = (marked_equity * 0.01) / float(session["pending_atr"])
            affordable = cash / (fill_price * (1.0 + fee_rate))
            fill_quantity = max(0.0, min(risk_quantity, affordable))
            if fill_quantity > 0:
                previous_quantity = quantity
                notional = fill_quantity * fill_price
                fee = notional * fee_rate
                cash -= notional + fee
                quantity += fill_quantity
                average_entry = (
                    average_entry * previous_quantity + fill_price * fill_quantity
                ) / quantity
                units += 1
                last_add_price = fill_price
                fill_side = "BUY"
        elif pending == "EXIT" and quantity > 0:
            fill_price = float(current.open) * (1.0 - slippage_rate)
            fill_quantity = quantity
            notional = fill_quantity * fill_price
            fee = notional * fee_rate
            cash += notional - fee
            quantity = 0.0
            average_entry = 0.0
            units = 0
            last_add_price = 0.0
            fill_side = "SELL"

        atr = _atr(completed)
        prior = completed[:-1]
        entry_channel = max(float(bar.high) for bar in prior[-entry_window:])
        exit_channel = min(float(bar.low) for bar in prior[-exit_window:])
        close = float(current.close)
        if quantity > 0 and close < exit_channel:
            signal, reason = "EXIT", f"close below prior {exit_window}-bar exit channel"
        elif quantity <= 0 and close > entry_channel:
            signal, reason = "ENTER", f"close above prior {entry_window}-bar entry channel"
        elif quantity > 0 and units < 4 and close >= last_add_price + 0.5 * atr:
            signal, reason = "ADD", "close advanced at least 0.5 ATR from the last unit"
        else:
            signal, reason = "HOLD", "no completed-candle Turtle trigger"

        canonical_bars = json.dumps(
            [bar.model_dump(mode="json") for bar in completed],
            sort_keys=True,
            separators=(",", ":"),
        )
        input_sha256 = hashlib.sha256(canonical_bars.encode("utf-8")).hexdigest()
        equity = cash + quantity * close
        decision_id = str(uuid.uuid4())
        created_at = _now()
        connection.execute(
            """
            INSERT INTO shadow_decisions
                (id, session_id, completed_at, signal, reason, fill_side,
                 fill_quantity, fill_price, fee, close_price, cash,
                 position_quantity, average_entry_price, equity, input_sha256,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id, session_id, completed_at, signal, reason, fill_side,
                fill_quantity, fill_price, fee, close, cash, quantity,
                average_entry, equity, input_sha256, created_at,
            ),
        )
        connection.execute(
            """
            UPDATE shadow_sessions
            SET cash = ?, quantity = ?, average_entry_price = ?, units = ?,
                last_add_price = ?, pending_action = ?, pending_signal_at = ?,
                pending_atr = ?, last_completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                cash, quantity, average_entry, units, last_add_price,
                None if signal == "HOLD" else signal,
                None if signal == "HOLD" else completed_at,
                None if signal == "HOLD" else atr,
                completed_at, created_at, session_id,
            ),
        )
        result = connection.execute(
            "SELECT * FROM shadow_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
    return _row(result)
