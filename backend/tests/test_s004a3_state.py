"""Contract tests for the pinned per-bar causal state exporter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.nemt_strategy.s004a3_state import (
    causal_direction_states, trend_age,
)
from emergenzix_api.schemas import Bar

START = datetime(2023, 1, 2, tzinfo=timezone.utc)


def ramp(n: int, step: float) -> list[Bar]:
    bars, price = [], 100.0
    for i in range(n):
        price = price * (1 + step)
        bars.append(Bar(timestamp=START + timedelta(hours=4 * i), open=price,
                        high=price * 1.001, low=price * 0.999, close=price, volume=1.0))
    return bars


def test_returns_one_label_per_bar() -> None:
    bars = ramp(60, 0.01)
    states = causal_direction_states(bars)
    assert len(states) == len(bars)
    assert set(states) <= {"LONG", "SHORT", "NEUTRAL"}


def test_warmup_bars_are_neutral() -> None:
    states = causal_direction_states(ramp(20, 0.01))
    assert states[:4] == ["NEUTRAL"] * 4


def test_uptrend_labels_long() -> None:
    states = causal_direction_states(ramp(80, 0.01))
    assert states[-1] == "LONG"


def test_downtrend_labels_short() -> None:
    states = causal_direction_states(ramp(80, -0.01))
    assert states[-1] == "SHORT"


def test_states_are_causal_prefix_only() -> None:
    """Appending future bars must not change any past label."""
    bars = ramp(80, 0.01)
    full = causal_direction_states(bars)
    prefix = causal_direction_states(bars[:50])
    assert prefix == full[:50]


def test_trend_age_counts_consecutive_agreement() -> None:
    states = ["NEUTRAL", "LONG", "LONG", "LONG", "SHORT"]
    assert trend_age(states, 3, "LONG") == 3
    assert trend_age(states, 4, "SHORT") == 1
    assert trend_age(states, 0, "LONG") == 0


def test_trend_age_rejects_bad_direction() -> None:
    with pytest.raises(ValueError):
        trend_age(["LONG"], 0, "SIDEWAYS")


def test_empty_input_is_handled() -> None:
    assert causal_direction_states([]) == []


def test_windowed_slice_matches_full_prefix_call() -> None:
    """The fast path must be identical to passing the entire prefix."""
    from emergenzix_api.nemt_strategy.nemt_momentum_v2 import compute_momentum_state
    from emergenzix_api.nemt_strategy.v522_ablation_cli import _direction

    bars = ramp(400, 0.003)
    closes = [b.close for b in bars]
    fast = causal_direction_states(bars)
    for index in (5, 6, 7, 50, 119, 120, 121, 250, 399):
        prefix = closes[:index + 1]
        d = _direction(compute_momentum_state(prefix))
        expected = "LONG" if d == 1 else ("SHORT" if d == -1 else "NEUTRAL")
        assert fast[index] == expected, index
