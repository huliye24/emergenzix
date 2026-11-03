from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.walkforward import (
    build_walk_forward_windows,
    resample_candles,
    run_turtle_walk_forward,
    validate_base_candles,
)


def _rows(count: int, *, start: datetime) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        price = 100.0 + index * 0.01
        rows.append(
            {
                "timestamp": start + timedelta(minutes=15 * index),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.25,
                "base_volume": 10.0,
                "is_closed": True,
                "is_imputed": False,
            }
        )
    return rows


def test_lake_range_rejects_gap_and_imputed_rows() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = _rows(8, start=start)
    rows.pop(3)
    with pytest.raises(ValueError, match="incomplete"):
        validate_base_candles(rows, start=start, end=start + timedelta(hours=2))

    rows = _rows(8, start=start)
    rows[3]["is_imputed"] = True
    with pytest.raises(ValueError, match="imputed candle"):
        validate_base_candles(rows, start=start, end=start + timedelta(hours=2))


def test_resample_and_walk_forward_use_complete_oos_folds() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=12)
    selected = validate_base_candles(
        _rows(12 * 24 * 4, start=start), start=start, end=end
    )
    bars = resample_candles(selected, timeframe="1h")
    windows = build_walk_forward_windows(
        start=start,
        end=end,
        train_days=4,
        test_days=2,
        step_days=2,
    )

    result = run_turtle_walk_forward(
        bars,
        windows=windows,
        entry_window=20,
        exit_window=10,
        initial_cash=10_000.0,
        fee_bps=10.0,
        slippage_bps=5.0,
    )

    assert len(bars) == 12 * 24
    assert result["fold_count"] == 4
    assert all(fold["oos_bars"] == 2 * 24 for fold in result["folds"])
    assert result["number_of_trades"] >= 0
