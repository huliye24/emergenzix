from __future__ import annotations

from datetime import datetime, timedelta, timezone

from emergenzix_api.backtesting import run_donchian_breakout
from emergenzix_api.schemas import Bar


def _bar(index: int, *, open_price: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc)
        + timedelta(hours=4 * index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _run(bars: list[Bar]) -> dict[str, object]:
    return run_donchian_breakout(
        bars,
        entry_window=20,
        exit_window=10,
        initial_cash=10_000.0,
        fee_bps=10.0,
        slippage_bps=5.0,
    )


def test_donchian_enters_and_exits_at_next_bar_open() -> None:
    bars = [_bar(i, open_price=100.0, high=101.0, low=99.0, close=100.0) for i in range(30)]
    bars[20] = _bar(20, open_price=100.0, high=103.0, low=99.0, close=102.0)
    bars[21] = _bar(21, open_price=103.0, high=104.0, low=101.0, close=103.0)
    bars[24] = _bar(24, open_price=100.0, high=101.0, low=97.0, close=98.0)
    bars[25] = _bar(25, open_price=97.0, high=98.0, low=96.0, close=97.0)

    result = _run(bars)

    assert result["number_of_trades"] == 1
    trade = result["trades"][0]
    assert trade["entry_timestamp"] == bars[21].timestamp.isoformat()
    assert trade["exit_timestamp"] == bars[25].timestamp.isoformat()
    assert trade["entry_price"] == 103.0515
    assert trade["exit_price"] == 96.9515


def test_donchian_channel_excludes_signal_candle_high() -> None:
    bars = [_bar(i, open_price=100.0, high=101.0, low=99.0, close=100.0) for i in range(24)]
    bars[20] = _bar(20, open_price=100.0, high=1000.0, low=99.0, close=102.0)
    bars[21] = _bar(21, open_price=103.0, high=104.0, low=101.0, close=103.0)

    result = _run(bars)

    assert result["number_of_trades"] == 1
    assert result["trades"][0]["entry_timestamp"] == bars[21].timestamp.isoformat()


def test_donchian_does_not_trade_before_warmup() -> None:
    bars = [_bar(i, open_price=100.0, high=101.0, low=99.0, close=100.0) for i in range(24)]
    bars[19] = _bar(19, open_price=100.0, high=103.0, low=99.0, close=102.0)

    result = _run(bars)

    assert result["number_of_trades"] == 0
    assert result["final_equity"] == 10_000.0
