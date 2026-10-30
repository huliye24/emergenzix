from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.binance_market_data_v2_incremental import merge_closed_tail


UTC = timezone.utc


def candle(index: int, *, api: bool = False):
    opened = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * index)
    values = {
        "open": 100 + index, "high": 102 + index,
        "low": 99 + index, "close": 101 + index,
        "base_volume": 10 + index, "quote_volume": 1000 + index,
        "trade_count": 20 + index, "taker_buy_base_volume": 4 + index,
        "taker_buy_quote_volume": 400 + index,
    }
    if api:
        return {"timestamp": opened.isoformat(), **values}
    return {
        "timestamp": opened, "close_time": opened + timedelta(minutes=15),
        "available_at": opened + timedelta(minutes=15), **values,
        "is_closed": True, "is_imputed": False,
    }


def test_increment_preserves_stored_availability_and_adds_contiguous_tail() -> None:
    stored = [candle(index) for index in range(10)]
    tail = [candle(index, api=True) for index in range(4, 12)]
    observed = datetime(2024, 1, 1, 3, 5, tzinfo=UTC)
    merged, added, overlap = merge_closed_tail(
        stored, tail, completed=datetime(2024, 1, 1, 2, 45, tzinfo=UTC),
        observed_at=observed,
    )
    assert (added, overlap) == (2, 6)
    assert merged[9]["available_at"] == stored[9]["available_at"]
    assert merged[-1]["available_at"] == observed


def test_increment_rejects_source_revision_or_new_gap() -> None:
    stored = [candle(index) for index in range(10)]
    revised = [candle(index, api=True) for index in range(4, 11)]
    revised[0]["close"] += 1
    with pytest.raises(ValueError, match="source mismatch"):
        merge_closed_tail(
            stored, revised, completed=datetime(2024, 1, 1, 3, tzinfo=UTC),
            observed_at=datetime(2024, 1, 1, 3, 5, tzinfo=UTC),
        )
    gapped = [candle(index, api=True) for index in [4, 5, 6, 7, 8, 9, 11]]
    with pytest.raises(ValueError, match="new gap"):
        merge_closed_tail(
            stored, gapped, completed=datetime(2024, 1, 1, 3, tzinfo=UTC),
            observed_at=datetime(2024, 1, 1, 3, 5, tzinfo=UTC),
        )
