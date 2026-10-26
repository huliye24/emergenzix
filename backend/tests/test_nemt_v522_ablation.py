"""Execution-contract tests for the NEMT v5.2.2 ablation engine.

The four properties locked here are exactly the ones tracked separately by the
v5.2.1 (channel causality), v5.2.3 (entry-bar protective stop) and v5.2.4
(gap-through stop, exit ordering, no same-bar re-entry) methodology notes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from emergenzix_api.nemt_strategy import v522_ablation_cli as engine
from emergenzix_api.nemt_strategy.nemt_momentum_v2 import MomentumState
from emergenzix_api.schemas import Bar

ZONE = ZoneInfo("Asia/Shanghai")
T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _bars(closes: list[float]) -> list[Bar]:
    out: list[Bar] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        open_price = previous if index else close
        out.append(Bar(
            timestamp=T0 + timedelta(hours=4) * index,
            open=open_price, high=max(open_price, close),
            low=min(open_price, close), close=close, volume=1.0,
        ))
        previous = close
    return out


def _replace(bar: Bar, **kwargs) -> Bar:
    payload = {"timestamp": bar.timestamp, "open": bar.open, "high": bar.high,
               "low": bar.low, "close": bar.close, "volume": bar.volume}
    payload.update(kwargs)
    return Bar(**payload)


def _always_critical_up(monkeypatch) -> None:
    monkeypatch.setattr(
        engine, "compute_momentum_state",
        lambda closes, config=None: MomentumState(1.0, 0.9, "critical_up", 90),
    )


def test_breakout_channel_excludes_the_decision_bar() -> None:
    """v5.2.1: a decision bar's own high must never enter its own channel."""
    closes = [100.0] * 130 + [120.0]
    bars = _bars(closes)
    bars[130] = _replace(bars[130], open=100.0, low=100.0, high=150.0, close=120.0)

    assert engine.breakout(bars, 130, 120) == 1


def test_slow_arm_channel_is_wider_than_fast_arm() -> None:
    """A 120-bar breakout that fails the 330-bar channel separates the arms."""
    # A spike at index 100 sits inside the 330-bar window but outside the
    # 120-bar window of the final bar, so only the slow arm is blocked.
    closes = [100.0] * 400
    closes[100] = 300.0
    closes[399] = 150.0
    bars = _bars(closes)
    index = len(bars) - 1

    assert engine.breakout(bars, index, 120) == 1
    assert engine.breakout(bars, index, 330) == 0


def test_protective_stop_is_active_inside_the_entry_bar(monkeypatch) -> None:
    """v5.2.3: a position must be stoppable on the bar that opened it."""
    _always_critical_up(monkeypatch)
    bars = _bars([100.0 + 0.01 * index for index in range(200)])
    entry_index = max(engine.ARMS["A"].entry_bars, engine.ATR_BARS) + 2
    bars[entry_index] = _replace(bars[entry_index], low=1.0)

    report = engine.run_arm(
        bars=bars, arm=engine.ARMS["A"], zone=ZONE,
        start=T0, end=T0 + timedelta(hours=4 * 400),
    )

    first = report["trades"][0]
    assert first["entry_index"] == entry_index
    assert first["exit_index"] == entry_index
    assert first["holding_bars"] == 0
    assert first["exit_reason"] == "2N_stop"


def test_gap_through_stop_fills_at_the_open_not_the_stop(monkeypatch) -> None:
    """v5.2.4: a gapped open may not be filled at the pre-existing stop price."""
    _always_critical_up(monkeypatch)
    bars = _bars([100.0 + 0.01 * index for index in range(200)])
    entry_index = max(engine.ARMS["A"].entry_bars, engine.ATR_BARS) + 2
    gap_open = bars[entry_index].close * 0.90
    bars[entry_index + 1] = _replace(
        bars[entry_index + 1],
        open=gap_open, high=gap_open * 1.01, low=gap_open * 0.99, close=gap_open,
    )

    report = engine.run_arm(
        bars=bars, arm=engine.ARMS["A"], zone=ZONE,
        start=T0, end=T0 + timedelta(hours=4 * 400),
    )

    first = report["trades"][0]
    assert first["exit_reason"] == "gap_stop"
    assert first["exit_index"] == entry_index + 1
    expected = gap_open * (1 - engine.SLIPPAGE_RATE)
    assert first["exit_price"] == pytest.approx(expected)
    assert first["exit_price"] < first["stop"]


def test_no_entry_on_a_bar_that_already_carried_a_position(monkeypatch) -> None:
    """Exit ordering must not permit a same-bar round trip at the same price."""
    _always_critical_up(monkeypatch)
    bars = _bars([100.0 + 0.01 * index for index in range(200)])
    entry_index = max(engine.ARMS["A"].entry_bars, engine.ATR_BARS) + 2
    gap_open = bars[entry_index].close * 0.90
    bars[entry_index + 1] = _replace(
        bars[entry_index + 1],
        open=gap_open, high=gap_open * 1.01, low=gap_open * 0.99, close=gap_open,
    )

    report = engine.run_arm(
        bars=bars, arm=engine.ARMS["A"], zone=ZONE,
        start=T0, end=T0 + timedelta(hours=4 * 400),
    )

    trades = report["trades"]
    assert len(trades) >= 2
    assert trades[1]["entry_index"] > trades[0]["exit_index"]


def test_censored_entries_are_excluded_from_the_primary_denominator(monkeypatch) -> None:
    """Trades still open at the window edge must not enter the early-stop ratio."""
    _always_critical_up(monkeypatch)
    bars = _bars([100.0 + 0.01 * index for index in range(200)])
    end = bars[155].timestamp.astimezone(ZONE) + timedelta(hours=4)

    report = engine.run_arm(
        bars=bars, arm=engine.ARMS["A"], zone=ZONE,
        start=T0, end=end, observation_bars=42,
    )

    counters = report["counters"]
    assert counters["actual_entries"] >= 1
    assert counters["censored_entries"] >= 1
    assert counters["observable_entries"] + counters["censored_entries"] == counters["actual_entries"]
