from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.market_candle_v2 import aggregate_candles_v2, normalize_candle_v2


UTC = timezone.utc


def candle(index: int, **overrides):
    opened = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * index)
    value = {
        "timestamp": opened,
        "close_time": opened + timedelta(minutes=15),
        "available_at": opened + timedelta(minutes=15, seconds=2),
        "open": 100 + index,
        "high": 102 + index,
        "low": 99 + index,
        "close": 101 + index,
        "base_volume": 10 + index,
        "quote_volume": 1000 + index,
        "trade_count": 20 + index,
        "taker_buy_base_volume": 4 + index,
        "taker_buy_quote_volume": 400 + index,
        "is_closed": True,
        "is_imputed": False,
    }
    return {**value, **overrides}


def test_normalization_preserves_null_instead_of_converting_it_to_zero() -> None:
    normalized = normalize_candle_v2(candle(0, quote_volume=None, trade_count=None))
    assert normalized["quote_volume"] is None
    assert normalized["trade_count"] is None


def test_normalization_blocks_lookahead_and_invalid_volume() -> None:
    with pytest.raises(ValueError, match="cannot precede"):
        normalize_candle_v2(candle(0, available_at=datetime(2024, 1, 1, tzinfo=UTC)))
    with pytest.raises(ValueError, match="exceeds total"):
        normalize_candle_v2(candle(0, taker_buy_base_volume=11))
    with pytest.raises(ValueError, match="must be an integer"):
        normalize_candle_v2(candle(0, trade_count=1.5))


def test_aggregation_sums_available_metrics_and_propagates_unknowns() -> None:
    source = [candle(index) for index in range(4)]
    source[2]["trade_count"] = None
    result = aggregate_candles_v2(source, target_interval=timedelta(hours=1))
    assert result["timestamp"] == datetime(2024, 1, 1, tzinfo=UTC)
    assert result["close_time"] == datetime(2024, 1, 1, 1, tzinfo=UTC)
    assert result["available_at"] == source[-1]["available_at"]
    assert result["base_volume"] == 46
    assert result["quote_volume"] == 4006
    assert result["trade_count"] is None


def test_aggregation_rejects_incomplete_or_noncontiguous_input() -> None:
    with pytest.raises(ValueError, match="multiple of 15 minutes"):
        aggregate_candles_v2([candle(0)], target_interval=timedelta(minutes=20))
    with pytest.raises(ValueError, match="incomplete"):
        aggregate_candles_v2([candle(0), candle(1)], target_interval=timedelta(hours=1))
    with pytest.raises(ValueError, match="not contiguous"):
        aggregate_candles_v2(
            [candle(0), candle(1), candle(3), candle(4)],
            target_interval=timedelta(hours=1),
        )
