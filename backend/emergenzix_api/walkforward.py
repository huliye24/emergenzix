"""Deterministic, fail-closed walk-forward research utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import prod
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from .backtesting import run_turtle_breakout

BASE_INTERVAL = timedelta(minutes=15)
TIMEFRAME_INTERVALS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}
TURTLE_SYSTEMS = {
    "turtle-20-10": (20, 10),
    "turtle-55-20": (55, 20),
}


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def validate_base_candles(
    rows: Iterable[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> list[Mapping[str, Any]]:
    """Select a closed 15-minute range and reject gaps or synthetic rows."""
    start = _utc(start)
    end = _utc(end)
    if start >= end or start.minute % 15 or end.minute % 15:
        raise ValueError("start/end must define an aligned, non-empty 15m range")

    selected = sorted(
        (row for row in rows if start <= _utc(row["timestamp"]) < end),
        key=lambda row: _utc(row["timestamp"]),
    )
    expected_count = int((end - start) / BASE_INTERVAL)
    if len(selected) != expected_count:
        raise ValueError(
            f"15m range is incomplete: expected {expected_count}, got {len(selected)}"
        )

    for index, row in enumerate(selected):
        timestamp = _utc(row["timestamp"])
        expected_timestamp = start + index * BASE_INTERVAL
        if timestamp != expected_timestamp:
            raise ValueError(
                f"15m gap or duplicate at {expected_timestamp.isoformat()}"
            )
        if not bool(row.get("is_closed", False)):
            raise ValueError(f"forming candle at {timestamp.isoformat()}")
        if bool(row.get("is_imputed", False)):
            raise ValueError(f"imputed candle at {timestamp.isoformat()}")
        _validate_ohlcv(row, timestamp)
    return selected


def resample_candles(
    rows: list[Mapping[str, Any]],
    *,
    timeframe: str,
) -> list[SimpleNamespace]:
    """Aggregate verified 15m candles into complete UTC-aligned bars."""
    try:
        interval = TIMEFRAME_INTERVALS[timeframe]
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe: {timeframe}") from exc
    children_per_bar = int(interval / BASE_INTERVAL)
    if len(rows) % children_per_bar:
        raise ValueError(f"15m range does not contain complete {timeframe} bars")

    bars: list[SimpleNamespace] = []
    for offset in range(0, len(rows), children_per_bar):
        group = rows[offset : offset + children_per_bar]
        timestamp = _utc(group[0]["timestamp"])
        if timestamp != _floor_utc(timestamp, interval):
            raise ValueError(f"range is not aligned to {timeframe}: {timestamp.isoformat()}")
        expected_last = timestamp + (children_per_bar - 1) * BASE_INTERVAL
        if _utc(group[-1]["timestamp"]) != expected_last:
            raise ValueError(f"incomplete {timeframe} bar at {timestamp.isoformat()}")
        bars.append(
            SimpleNamespace(
                timestamp=timestamp,
                open=float(group[0]["open"]),
                high=max(float(row["high"]) for row in group),
                low=min(float(row["low"]) for row in group),
                close=float(group[-1]["close"]),
                volume=sum(float(row.get("base_volume", row.get("volume", 0.0))) for row in group),
            )
        )
    return bars


def build_walk_forward_windows(
    *,
    start: datetime,
    end: datetime,
    train_days: int,
    test_days: int,
    step_days: int,
) -> list[WalkForwardWindow]:
    """Build rolling train/test windows; partial final tests are excluded."""
    if min(train_days, test_days, step_days) <= 0:
        raise ValueError("walk-forward day counts must be positive")
    start = _utc(start)
    end = _utc(end)
    windows: list[WalkForwardWindow] = []
    train_start = start
    while True:
        train_end = train_start + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
            )
        )
        train_start += timedelta(days=step_days)
    if not windows:
        raise ValueError("date range is too short for one complete walk-forward fold")
    return windows


def run_turtle_walk_forward(
    bars: list[Any],
    *,
    windows: list[WalkForwardWindow],
    entry_window: int,
    exit_window: int,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    """Evaluate a fixed Turtle configuration on isolated OOS folds."""
    folds: list[dict[str, Any]] = []
    total_wins = 0
    for fold_index, window in enumerate(windows):
        fold_bars = [
            bar
            for bar in bars
            if window.train_start <= _utc(bar.timestamp) < window.test_end
        ]
        evaluation_start_index = next(
            (
                index
                for index, bar in enumerate(fold_bars)
                if _utc(bar.timestamp) >= window.test_start
            ),
            -1,
        )
        if evaluation_start_index < 0:
            raise ValueError(f"fold {fold_index} has no out-of-sample candles")
        result = run_turtle_breakout(
            fold_bars,
            entry_window=entry_window,
            exit_window=exit_window,
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            evaluation_start_index=evaluation_start_index,
        )
        wins = sum(1 for trade in result["trades"] if trade["net_pnl"] > 0)
        total_wins += wins
        folds.append(
            {
                "fold": fold_index + 1,
                "train_start": window.train_start.isoformat(),
                "train_end": window.train_end.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "oos_bars": result["bars_count"],
                "total_return_pct": result["total_return_pct"],
                "max_drawdown_pct": result["max_drawdown_pct"],
                "number_of_trades": result["number_of_trades"],
                "win_rate_pct": result["win_rate_pct"],
                "benchmark_return_pct": result["benchmark_return_pct"],
            }
        )

    trades = sum(int(fold["number_of_trades"]) for fold in folds)
    compounded_return = (
        prod(1.0 + float(fold["total_return_pct"]) / 100.0 for fold in folds) - 1.0
    ) * 100.0
    compounded_benchmark = (
        prod(
            1.0 + float(fold["benchmark_return_pct"]) / 100.0 for fold in folds
        )
        - 1.0
    ) * 100.0
    return {
        "folds": folds,
        "fold_count": len(folds),
        "compounded_oos_return_pct": round(compounded_return, 6),
        "worst_fold_drawdown_pct": max(
            float(fold["max_drawdown_pct"]) for fold in folds
        ),
        "number_of_trades": trades,
        "win_rate_pct": round(total_wins / trades * 100.0, 6) if trades else 0.0,
        "compounded_benchmark_return_pct": round(compounded_benchmark, 6),
    }


def _validate_ohlcv(row: Mapping[str, Any], timestamp: datetime) -> None:
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    volume = float(row.get("base_volume", row.get("volume", 0.0)))
    if min(open_price, high, low, close) <= 0 or volume < 0:
        raise ValueError(f"invalid OHLCV values at {timestamp.isoformat()}")
    if high < max(open_price, close) or low > min(open_price, close) or low > high:
        raise ValueError(f"inconsistent OHLC values at {timestamp.isoformat()}")


def _floor_utc(value: datetime, interval: timedelta) -> datetime:
    value = _utc(value)
    seconds = int(interval.total_seconds())
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
