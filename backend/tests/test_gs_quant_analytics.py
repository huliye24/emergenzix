from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.gs_quant_analytics import analyze_prices


def price_series(*, offset: float = 0.0, count: int = 40) -> dict[datetime, float]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        start + timedelta(days=index): 100.0 + offset + index + (index % 4) * 0.25
        for index in range(count)
    }


def test_calculates_offline_timeseries_diagnostics() -> None:
    result = analyze_prices(price_series(), benchmark_prices=price_series(offset=100), window=14)

    assert result["source"] == "gs-quant"
    assert result["source_version"] == "2.2.3"
    assert result["observations"] == 40
    assert result["window"] == 14
    assert result["bollinger_lower"] < result["bollinger_upper"]
    assert 0 <= result["rsi"] <= 100
    assert result["realized_volatility_pct"] >= 0
    assert result["max_drawdown_fraction"] <= 0
    assert result["beta"] is not None


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan"), float("inf")])
def test_rejects_unsafe_price_values_before_analysis(bad_value: float) -> None:
    prices = price_series()
    prices[next(iter(prices))] = bad_value
    with pytest.raises(ValueError, match="positive and finite"):
        analyze_prices(prices)


def test_rejects_naive_timestamps() -> None:
    prices = {
        datetime(2026, 1, 1) + timedelta(days=index): 100.0 + index
        for index in range(20)
    }
    with pytest.raises(ValueError, match="timezone-aware"):
        analyze_prices(prices)


def test_rejects_insufficient_benchmark_overlap() -> None:
    benchmark = {
        timestamp + timedelta(days=100): value
        for timestamp, value in price_series().items()
    }
    with pytest.raises(ValueError, match="aligned observations"):
        analyze_prices(price_series(), benchmark_prices=benchmark)
