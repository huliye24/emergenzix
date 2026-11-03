"""Deterministic, PAPER-only BTC short strategy comparison runner."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any

from .database import Database
from .schemas import Bar
from .watch_agent import TIMEFRAME_SECONDS, _parse_completed_bars


class ShortShadowRunnerError(ValueError):
    """A fail-closed short-shadow configuration or market-data error."""


STRATEGIES: dict[str, dict[str, Any]] = {
    "donchian_breakdown_20_10": {
        "name": "Donchian Breakdown 20/10",
        "entry_window": 20,
        "exit_window": 10,
        "atr_window": 20,
        "risk_pct": 0.01,
        "stop_atr": 2.0,
    },
    "ema_bear_trend_20_50": {
        "name": "EMA Bear Trend 20/50",
        "fast_window": 20,
        "slow_window": 50,
        "atr_window": 20,
        "risk_pct": 0.01,
        "stop_atr": 2.0,
    },
    "bollinger_breakdown_20_2": {
        "name": "Bollinger Breakdown 20/2",
        "window": 20,
        "standard_deviations": 2.0,
        "atr_window": 20,
        "risk_pct": 0.01,
        "stop_atr": 2.0,
    },
}
STRATEGY_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(value: Any) -> dict[str, Any]:
    return dict(value)


def _fingerprint(strategy_key: str, parameters: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"key": strategy_key, "parameters": parameters, "version": STRATEGY_VERSION},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_btc_short_fleet(
    db: Database,
    *,
    timeframe: str = "1h",
    initial_cash: float = 10_000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    """Create three isolated 10k-style comparison accounts; never an exchange account."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise ShortShadowRunnerError("unsupported shadow timeframe")
    if initial_cash <= 0 or fee_bps < 0 or slippage_bps < 0:
        raise ShortShadowRunnerError("cash must be positive and costs cannot be negative")
    fleet_id = str(uuid.uuid4())
    created_at = _now()
    session_ids: list[str] = []
    with db.connection() as connection:
        for strategy_key, parameters in STRATEGIES.items():
            session_id = str(uuid.uuid4())
            session_ids.append(session_id)
            parameters_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
            connection.execute(
                """
                INSERT INTO short_shadow_sessions
                    (id, fleet_id, strategy_key, strategy_version, strategy_sha256,
                     parameters_json, symbol, timeframe, initial_cash, fee_bps,
                     slippage_bps, cash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'BTCUSDT', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    fleet_id,
                    strategy_key,
                    STRATEGY_VERSION,
                    _fingerprint(strategy_key, parameters),
                    parameters_json,
                    timeframe,
                    initial_cash,
                    fee_bps,
                    slippage_bps,
                    initial_cash,
                    created_at,
                    created_at,
                ),
            )
        rows = connection.execute(
            "SELECT * FROM short_shadow_sessions WHERE fleet_id = ? ORDER BY strategy_key",
            (fleet_id,),
        ).fetchall()
    return {
        "fleet_id": fleet_id,
        "execution_mode": "PAPER_ONLY",
        "live_order_permission": False,
        "capital_model": "three independent comparison accounts",
        "sessions": [_row(row) for row in rows],
    }


def list_short_shadow_sessions(db: Database) -> list[dict[str, Any]]:
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM short_shadow_sessions ORDER BY created_at DESC, strategy_key"
        ).fetchall()
    return [_row(row) for row in rows]


def get_short_shadow_session(db: Database, session_id: str) -> dict[str, Any] | None:
    with db.connection() as connection:
        session = connection.execute(
            "SELECT * FROM short_shadow_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return None
        decisions = connection.execute(
            """
            SELECT * FROM short_shadow_decisions
            WHERE session_id = ? ORDER BY completed_at DESC LIMIT 200
            """,
            (session_id,),
        ).fetchall()
    result = _row(session)
    result["decisions"] = [_row(row) for row in decisions]
    result["execution_mode"] = "PAPER_ONLY"
    result["live_order_permission"] = False
    return result


def _atr(bars: list[Bar], window: int = 20) -> float:
    ranges: list[float] = []
    for index in range(len(bars) - window, len(bars)):
        bar = bars[index]
        previous_close = float(bars[index - 1].close)
        ranges.append(
            max(
                float(bar.high) - float(bar.low),
                abs(float(bar.high) - previous_close),
                abs(float(bar.low) - previous_close),
            )
        )
    value = sum(ranges) / window
    if not math.isfinite(value) or value <= 0:
        raise ShortShadowRunnerError("ATR must be positive and finite")
    return value


def _ema(values: list[float], window: int) -> float:
    alpha = 2.0 / (window + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _strategy_signal(
    strategy_key: str,
    bars: list[Bar],
    *,
    has_position: bool,
    stop_price: float,
) -> tuple[str, str]:
    close = float(bars[-1].close)
    prior = bars[:-1]
    if has_position and close >= stop_price:
        return "COVER", "close reached the fixed 2 ATR protective stop"
    if strategy_key == "donchian_breakdown_20_10":
        if has_position and close > max(float(bar.high) for bar in prior[-10:]):
            return "COVER", "close above prior 10-bar exit channel"
        if not has_position and close < min(float(bar.low) for bar in prior[-20:]):
            return "SHORT", "close below prior 20-bar breakdown channel"
    elif strategy_key == "ema_bear_trend_20_50":
        closes = [float(bar.close) for bar in bars]
        fast = _ema(closes, 20)
        slow = _ema(closes, 50)
        if has_position and (close > fast or fast >= slow):
            return "COVER", "EMA bearish regime ended"
        if not has_position and fast < slow and close < fast:
            return "SHORT", "EMA20 below EMA50 with price below EMA20"
    elif strategy_key == "bollinger_breakdown_20_2":
        previous_closes = [float(bar.close) for bar in prior[-20:]]
        middle = statistics.fmean(previous_closes)
        lower = middle - 2.0 * statistics.pstdev(previous_closes)
        if has_position and close >= middle:
            return "COVER", "close recovered to the prior 20-bar middle band"
        if not has_position and close < lower:
            return "SHORT", "close below prior 20-bar lower Bollinger band"
    else:
        raise ShortShadowRunnerError("unsupported short-shadow strategy")
    return "HOLD", "no completed-candle short trigger"


def run_short_shadow_cycle(
    db: Database,
    *,
    session_id: str,
    market_payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one immutable short-only decision for the newest completed candle."""
    try:
        completed = _parse_completed_bars(market_payload)
    except ValueError as exc:
        raise ShortShadowRunnerError(str(exc)) from exc
    current = completed[-1]
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ShortShadowRunnerError("shadow observation time must include a timezone")

    with db.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            "SELECT * FROM short_shadow_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise ShortShadowRunnerError("short-shadow session was not found")
        if session["status"] != "ACTIVE":
            raise ShortShadowRunnerError("short-shadow session is paused")
        parameters = json.loads(session["parameters_json"])
        if session["strategy_sha256"] != _fingerprint(session["strategy_key"], parameters):
            raise ShortShadowRunnerError("pinned strategy fingerprint no longer matches")
        symbol = str(market_payload.get("symbol", "")).upper().replace("/", "").replace("-", "")
        if symbol != "BTCUSDT" or market_payload.get("timeframe") != session["timeframe"]:
            raise ShortShadowRunnerError("market payload does not match the short-shadow session")

        completed_at = current.timestamp.isoformat()
        existing = connection.execute(
            "SELECT * FROM short_shadow_decisions WHERE session_id = ? AND completed_at = ?",
            (session_id, completed_at),
        ).fetchone()
        if existing is not None:
            return _row(existing)
        age = (observed_at - current.timestamp.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > TIMEFRAME_SECONDS[session["timeframe"]] * 3:
            raise ShortShadowRunnerError("latest completed candle is missing, stale, or in the future")
        if len(completed) < 52:
            raise ShortShadowRunnerError("at least 52 completed candles are required")

        cash = float(session["cash"])
        quantity = float(session["short_quantity"])
        average_entry = float(session["average_entry_price"])
        stop_price = float(session["stop_price"])
        fee_rate = float(session["fee_bps"]) / 10_000.0
        slippage_rate = float(session["slippage_bps"]) / 10_000.0
        fill_side: str | None = None
        fill_quantity = 0.0
        fill_price: float | None = None
        fee = 0.0

        pending = session["pending_action"]
        if pending == "SHORT" and quantity == 0 and session["pending_atr"]:
            fill_price = float(current.open) * (1.0 - slippage_rate)
            stop_distance = 2.0 * float(session["pending_atr"])
            risk_quantity = (cash * 0.01) / stop_distance
            one_x_quantity = cash / (fill_price * (1.0 + fee_rate))
            fill_quantity = max(0.0, min(risk_quantity, one_x_quantity))
            if fill_quantity > 0:
                fee = fill_quantity * fill_price * fee_rate
                cash -= fee
                quantity = fill_quantity
                average_entry = fill_price
                stop_price = fill_price + stop_distance
                fill_side = "SHORT"
        elif pending == "COVER" and quantity > 0:
            fill_price = float(current.open) * (1.0 + slippage_rate)
            fill_quantity = quantity
            fee = fill_quantity * fill_price * fee_rate
            cash = max(0.0, cash + fill_quantity * (average_entry - fill_price) - fee)
            quantity = 0.0
            average_entry = 0.0
            stop_price = 0.0
            fill_side = "COVER"

        atr = _atr(completed)
        signal, reason = _strategy_signal(
            session["strategy_key"],
            completed,
            has_position=quantity > 0,
            stop_price=stop_price,
        )
        close = float(current.close)
        equity = cash + quantity * (average_entry - close)
        if not math.isfinite(equity) or equity < 0:
            raise ShortShadowRunnerError("short account equity is invalid; cycle blocked")
        canonical_bars = json.dumps(
            [bar.model_dump(mode="json") for bar in completed],
            sort_keys=True,
            separators=(",", ":"),
        )
        input_sha256 = hashlib.sha256(canonical_bars.encode("utf-8")).hexdigest()
        decision_id = str(uuid.uuid4())
        created_at = _now()
        connection.execute(
            """
            INSERT INTO short_shadow_decisions
                (id, session_id, completed_at, signal, reason, fill_side,
                 fill_quantity, fill_price, fee, close_price, cash,
                 short_quantity, average_entry_price, equity, input_sha256, created_at)
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
            UPDATE short_shadow_sessions
            SET cash = ?, short_quantity = ?, average_entry_price = ?, stop_price = ?,
                pending_action = ?, pending_signal_at = ?, pending_atr = ?,
                last_completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                cash, quantity, average_entry, stop_price,
                None if signal == "HOLD" else signal,
                None if signal == "HOLD" else completed_at,
                None if signal == "HOLD" else atr,
                completed_at, created_at, session_id,
            ),
        )
        result = connection.execute(
            "SELECT * FROM short_shadow_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
    return _row(result)
