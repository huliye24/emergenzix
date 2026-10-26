"""Synthetic time-series tests for the S-004 causal trend classifier.

These tests exercise the contract mandated by
``strategies/candidates/S-004-sparse-trend-pyramid-hedge.md`` §52:
  * only completed weeks are visible
  * missing / incomplete weeks fail closed
  * the reported timestamp is the last completed week
  * future bars cannot change a past verdict
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.nemt_strategy.s004_trend_classifier import (
    CANDIDATE, CONFIGS, OBSERVE, REJECT, TrendConfig, classify,
)
from emergenzix_api.schemas import Bar

START = datetime(2023, 1, 2, tzinfo=timezone.utc)  # a Monday
WEEK = timedelta(days=7)
BARS_PER_WEEK = 42
FOUR_HOURS = timedelta(hours=4)


def flat_series(weeks: int, step: float = 1.0, base: float = 100.0) -> list[Bar]:
    """One bar per 4h; weekly closes rise (or fall) by `step` each week."""
    bars, price = [], base
    for week in range(weeks):
        price = base + step * week
        for candle in range(BARS_PER_WEEK):
            stamp = START + WEEK * week + FOUR_HOURS * candle
            bars.append(Bar(timestamp=stamp, open=price, high=price * 1.001,
                            low=price * 0.999, close=price, volume=1.0))
    return bars


def end_of(weeks: int) -> datetime:
    return START + WEEK * weeks


def test_rejects_insufficient_history() -> None:
    bars = flat_series(20)
    verdict = classify(bars, as_of=end_of(20), direction=1, config=CONFIGS["A1"])
    assert verdict.status == REJECT
    assert verdict.reason == "insufficient_history"
    assert verdict.last_completed_week is not None


def test_reports_last_completed_week_only() -> None:
    bars = flat_series(60)
    # as_of exactly at the start of week 56: weeks 0..55 are complete
    verdict = classify(bars, as_of=end_of(56), direction=1, config=CONFIGS["A1"])
    assert verdict.last_completed_week == (START + WEEK * 55).date().isoformat()


def test_uprising_series_is_a_candidate() -> None:
    bars = flat_series(60, step=1.0)
    verdict = classify(bars, as_of=end_of(60), direction=1, config=CONFIGS["A1"])
    assert verdict.status == CANDIDATE
    assert verdict.features["agreement"] == 3
    assert verdict.features["efficiency"] == pytest.approx(1.0, abs=1e-6)


def test_falling_series_rejects_long_and_accepts_short() -> None:
    bars = flat_series(60, step=-1.0)
    long_verdict = classify(bars, as_of=end_of(60), direction=1, config=CONFIGS["A1"])
    short_verdict = classify(bars, as_of=end_of(60), direction=-1, config=CONFIGS["A1"])
    assert long_verdict.status == REJECT
    assert long_verdict.reason == "direction_mismatch_26w"
    assert short_verdict.status == CANDIDATE


def test_missing_week_breaks_the_chain() -> None:
    bars = flat_series(60)
    # drop one whole week (week 40) -> chain must fail closed
    bars = [b for b in bars if not (START + WEEK * 40 <= b.timestamp < START + WEEK * 41)]
    verdict = classify(bars, as_of=end_of(60), direction=1, config=CONFIGS["A1"])
    assert verdict.status == REJECT
    assert verdict.reason == "broken_week_chain"


def test_incomplete_week_is_never_used() -> None:
    bars = flat_series(60)
    # only 10 of the 42 bars of week 59 exist
    partial = [b for b in bars if b.timestamp < START + WEEK * 59 + FOUR_HOURS * 10]
    verdict = classify(partial, as_of=end_of(60), direction=1, config=CONFIGS["A1"])
    assert verdict.last_completed_week == (START + WEEK * 58).date().isoformat()
    assert not verdict.features.get("agreements", {}).get("w13") is None


def test_future_bars_cannot_change_a_past_verdict() -> None:
    bars = flat_series(60)
    as_of = end_of(56)
    first = classify(bars, as_of=as_of, direction=1, config=CONFIGS["A1"])
    # append 20 future weeks that crash; timestamps stay strictly increasing
    extended = list(bars)
    for week in range(60, 80):
        price = 5.0
        for candle in range(BARS_PER_WEEK):
            stamp = START + WEEK * week + FOUR_HOURS * candle
            extended.append(Bar(timestamp=stamp, open=price, high=price, low=price,
                                close=price, volume=1.0))
    second = classify(extended, as_of=as_of, direction=1, config=CONFIGS["A1"])
    assert first.status == second.status
    assert first.last_completed_week == second.last_completed_week
    assert first.features["agreement"] == second.features["agreement"]


def test_efficiency_gate_rejects_choppy_series() -> None:
    # zig-zag upward: every window still points up, but the path is inefficient
    bars, price = [], 100.0
    for week in range(60):
        price += 1.0 if week % 2 == 0 else -0.60
        for candle in range(BARS_PER_WEEK):
            stamp = START + WEEK * week + FOUR_HOURS * candle
            bars.append(Bar(timestamp=stamp, open=price, high=price * 1.001,
                            low=price * 0.999, close=price, volume=1.0))
    verdict = classify(bars, as_of=end_of(60), direction=1, config=CONFIGS["A3"])
    assert verdict.status == REJECT
    assert verdict.reason.startswith("efficiency_")


def test_long_only_arm_rejects_shorts() -> None:
    bars = flat_series(60, step=-1.0)
    config = TrendConfig("long_only_probe", long_only=True)
    verdict = classify(bars, as_of=end_of(60), direction=-1, config=config)
    assert verdict.status == REJECT
    assert verdict.reason == "long_only_arm"


def test_invalid_series_is_rejected_not_crashing() -> None:
    bars = flat_series(60)
    # remove one bar inside week 55, which the last-53-week window does use
    drop = [i for i, b in enumerate(bars)
            if START + WEEK * 55 <= b.timestamp < START + WEEK * 56][20]
    broken = bars[:drop] + bars[drop + 1:]
    verdict = classify(broken, as_of=end_of(60), direction=1, config=CONFIGS["A1"])
    assert verdict.status == REJECT
    assert verdict.reason == "broken_week_chain"
    # a duplicate bar must also fail closed
    duplicated = bars[:drop] + [bars[drop]] + bars[drop:]
    verdict = classify(duplicated, as_of=end_of(60), direction=1, config=CONFIGS["A1"])
    assert verdict.status == REJECT


def test_naive_as_of_is_rejected() -> None:
    bars = flat_series(60)
    with pytest.raises(ValueError):
        classify(bars, as_of=datetime(2024, 1, 1), direction=1, config=CONFIGS["A1"])


def test_invalid_direction_is_rejected() -> None:
    bars = flat_series(60)
    with pytest.raises(ValueError):
        classify(bars, as_of=end_of(60), direction=0, config=CONFIGS["A1"])


def test_all_declared_configs_are_monotone_in_strictness() -> None:
    assert CONFIGS["A1"].min_efficiency <= CONFIGS["A2"].min_efficiency
    assert CONFIGS["A2"].min_efficiency <= CONFIGS["A3"].min_efficiency
    assert CONFIGS["A3"].min_efficiency <= CONFIGS["A5"].min_efficiency
    assert CONFIGS["A4"].min_agreement < CONFIGS["A1"].min_agreement
