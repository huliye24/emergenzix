"""Contract tests: S-004.A never looks ahead or produces executable orders."""
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.schemas import Bar
from emergenzix_api.sparse_trend_candidate import (
    SparseTrendConfig, classify_sparse_trend,
)

ORIGIN = datetime(2025, 1, 6, tzinfo=timezone.utc)
CFG = SparseTrendConfig(short_bars=3, long_bars=8, min_long_return=.06, min_efficiency=.30)


def candles(closes):
    return [
        Bar(timestamp=ORIGIN + timedelta(hours=4*i),
            open=v, high=v, low=v, close=v, volume=100)
        for i, v in enumerate(closes)
    ]


def observed(n):
    return ORIGIN + timedelta(hours=4*n)


def test_strong_sustained_trend_is_candidate():
    d = classify_sparse_trend(candles([100,101,102,103,104,105,106,107,110]), as_of=observed(9), config=CFG)
    assert d.state == "CANDIDATE"
    assert d.reason == "trend_candidate_only"
    assert d.last_closed_at_utc == observed(9).isoformat()


def test_flat_market_rejected_and_short_reversal_rejected():
    flat = classify_sparse_trend(candles([100]*9), as_of=observed(9), config=CFG)
    assert flat.state == "REJECT"
    assert flat.reason == "insufficient_long_trend"
    reversed_ = classify_sparse_trend(candles([100,101,102,105,110,112,111,109,108]), as_of=observed(9), config=CFG)
    assert reversed_.state == "REJECT"
    assert reversed_.reason == "short_trend_not_confirmed"


def test_choppy_rise_rejected():
    d = classify_sparse_trend(candles([100,120,98,121,99,122,100,123,124]), as_of=observed(9), config=CFG)
    assert d.state == "REJECT"
    assert d.reason == "inefficient_trend_path"


def test_future_data_and_incomplete_bar_cannot_change_decision():
    first = candles([100,101,102,103,104,105,106,107,110])
    future = candles([100,101,102,103,104,105,106,107,110,1,1])
    earlier = classify_sparse_trend(first, as_of=observed(9), config=CFG)
    assert earlier == classify_sparse_trend(future, as_of=observed(9), config=CFG)
    assert classify_sparse_trend(first, as_of=observed(8), config=CFG).state == "OBSERVE"


def test_gap_and_stale_data_fail_closed():
    source = candles([100,101,102,103,104,105,106,107,110])
    assert classify_sparse_trend(source[:4] + source[5:], as_of=observed(9), config=CFG).state == "OBSERVE"
    stale = classify_sparse_trend(source, as_of=observed(12), config=CFG)
    assert (stale.state, stale.reason) == ("OBSERVE", "stale_data")


def test_unsafe_timestamps_are_rejected():
    source = candles([100]*9)
    with pytest.raises(ValueError, match="timezone-aware"):
        classify_sparse_trend(source, as_of=datetime(2025, 1, 7), config=CFG)
    with pytest.raises(ValueError, match="strictly ordered"):
        classify_sparse_trend(source + [source[-1]], as_of=observed(10), config=CFG)
