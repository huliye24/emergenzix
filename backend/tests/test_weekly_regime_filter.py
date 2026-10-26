"""Causal unit tests for experimental weekly regime filter."""
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.schemas import Bar
from emergenzix_api.weekly_regime_filter import (
    completed_week_closes, permit_signal, up_up_entry_allowed,
)


START = datetime(2025, 1, 6, tzinfo=timezone.utc)  # Monday


def bars_for(weekly_prices):
    result = []
    for week, price in enumerate(weekly_prices):
        for candle in range(42):
            result.append(Bar(
                timestamp=START + timedelta(days=7*week, hours=4*candle),
                open=price, high=price, low=price, close=price, volume=100,
            ))
    return result


def end_of(weeks):
    return START + timedelta(weeks=weeks)


def test_only_completed_utc_weeks_are_visible():
    bars = bars_for([100, 101, 102, 103, 104, 105])
    assert len(completed_week_closes(bars, as_of=end_of(5))) == 5
    assert len(completed_week_closes(bars, as_of=end_of(6)-timedelta(seconds=1))) == 5
    assert len(completed_week_closes(bars, as_of=end_of(6))) == 6


def test_up_up_requires_six_consecutive_weeks():
    assert not up_up_entry_allowed(bars_for([100,101,102,103,104]), as_of=end_of(5))
    assert up_up_entry_allowed(bars_for([100,101,102,103,104,105]), as_of=end_of(6))
    assert not up_up_entry_allowed(bars_for([100,101,102,103,104,95]), as_of=end_of(6))


def test_missing_four_hour_bar_blocks_regime():
    bars = bars_for([100,101,102,103,104,105])
    bars.pop(84)
    assert not up_up_entry_allowed(bars, as_of=end_of(6))


def test_later_data_cannot_change_past_decision():
    first = bars_for([100,101,102,103,104,105])
    extended = bars_for([100,101,102,103,104,105,1,1])
    assert up_up_entry_allowed(first, as_of=end_of(6))
    assert up_up_entry_allowed(extended, as_of=end_of(6))


def test_exit_never_blocked_even_without_history():
    assert permit_signal([], as_of=end_of(1), signal="exit_long")
    assert not permit_signal([], as_of=end_of(1), signal="enter_long")
    with pytest.raises(ValueError, match="unknown signal"):
        permit_signal([], as_of=end_of(1), signal="short")


def test_timezone_naive_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        completed_week_closes([], as_of=datetime(2025, 2, 1))


def test_duplicate_candle_rejected():
    bars = bars_for([100])
    with pytest.raises(ValueError, match="strictly increasing"):
        completed_week_closes([bars[0], bars[0]], as_of=end_of(1))
