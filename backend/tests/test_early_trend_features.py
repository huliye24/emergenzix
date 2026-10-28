"""Synthetic, no-market-access contracts for S-004.A.3."""
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.early_trend_features import extract_early_trend_features
from emergenzix_api.schemas import Bar


START = datetime(2025, 1, 6, tzinfo=timezone.utc)


def build(n=130, *, constant=False):
    rows = []
    for i in range(n):
        price = 100.0 if constant else 100.0 + i + (i % 3) * .1
        rows.append(Bar(
            timestamp=START + timedelta(hours=4*i),
            open=price, high=price+1, low=price-1, close=price, volume=100,
        ))
    return rows


def cutoff(n):
    return START + timedelta(hours=4*n)


def test_all_four_features_available_with_causal_states():
    bars = build()
    states = ["NEUTRAL"] * 125 + ["LONG"] * 5
    result = extract_early_trend_features(
        bars, as_of=cutoff(130), direction="LONG", directional_states=states
    )
    assert result.feature_status == "AVAILABLE"
    assert result.trend_age_bars == 5
    assert 0 <= result.efficiency_42 <= 1
    assert result.vol_ratio_42_126 > 0
    assert 0 <= result.range_position_126 <= 1
    assert result.feature_cutoff_utc == cutoff(130).isoformat()


def test_missing_states_does_not_fabricate_trend_age():
    result = extract_early_trend_features(build(), as_of=cutoff(130), direction="LONG")
    assert result.feature_status == "PARTIAL"
    assert result.trend_age_bars is None


def test_future_candles_cannot_change_past_features():
    original = build(130)
    future = original + build(132)[130:]
    prior = extract_early_trend_features(original, as_of=cutoff(130), direction="LONG")
    assert prior == extract_early_trend_features(future, as_of=cutoff(130), direction="LONG")
    assert prior == extract_early_trend_features(future, as_of=cutoff(130)+timedelta(hours=1), direction="LONG")


def test_missing_history_and_gap_are_unavailable():
    assert extract_early_trend_features(build(126), as_of=cutoff(126), direction="LONG").reason == "insufficient_history"
    data = build()
    del data[42]
    assert extract_early_trend_features(data, as_of=cutoff(130), direction="LONG").reason == "missing_4h_candle"


def test_stale_and_zero_volatility_unavailable():
    assert extract_early_trend_features(build(), as_of=cutoff(133), direction="LONG").reason == "stale_data"
    assert extract_early_trend_features(build(constant=True), as_of=cutoff(130), direction="LONG").reason == "zero_long_volatility"


def test_invalid_time_and_direction_fail_closed():
    with pytest.raises(ValueError, match="timezone"):
        extract_early_trend_features(build(), as_of=datetime(2025, 2, 1), direction="LONG")
    with pytest.raises(ValueError, match="direction must"):
        extract_early_trend_features(build(), as_of=cutoff(130), direction="SIDEWAYS")
    with pytest.raises(ValueError, match="strictly increasing"):
        a = build()
        extract_early_trend_features(a + [a[-1]], as_of=cutoff(131), direction="LONG")
