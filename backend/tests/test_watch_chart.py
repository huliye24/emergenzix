from __future__ import annotations

from datetime import datetime, timedelta, timezone

from emergenzix_api.schemas import Bar
from emergenzix_api.watch_agent import WatchEvent
from emergenzix_api.watch_chart import (
    _bollinger_bands,
    _confirmed_trendline,
    render_watch_chart,
)


def test_watch_chart_renders_png_from_completed_bars() -> None:
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    bars = [
        Bar(
            timestamp=start + timedelta(hours=4 * index),
            open=80_000 + index * 10,
            high=80_200 + index * 10,
            low=79_800 + index * 10,
            close=80_100 + index * 10,
            volume=1_000 + index,
        )
        for index in range(30)
    ]
    event = WatchEvent(
        event_type="initial_snapshot",
        severity="info",
        occurred_at=start.isoformat(),
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="4h",
        message="Initial market scenario: range.",
        details={
            "scenario": "range",
            "confidence": 0.45,
            "key_levels": {"support": 79_800, "resistance": 81_000, "atr": 400},
            "trigger": "price remains between 79800 and 81000",
            "invalidation": "two completed closes outside the range buffer",
            "live_monitoring": {
                "timestamp": (start + timedelta(hours=4 * 30)).isoformat(),
                "open": 80_300,
                "high": 80_900,
                "low": 80_200,
                "price": 80_800,
                "volume": 2_000,
                "forming": True,
                "signal_input": False,
            },
        },
    )

    image = render_watch_chart(event, bars)

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 10_000


def test_bollinger_bands_use_only_completed_window() -> None:
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    bars = [
        Bar(
            timestamp=start + timedelta(hours=4 * index),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            volume=1_000,
        )
        for index in range(21)
    ]

    middle, upper, lower = _bollinger_bands(bars)

    assert len(middle) == 2
    assert middle == [109.5, 110.5]
    assert upper[1] > middle[1] > lower[1]


def test_trendline_uses_confirmed_rising_pivot_lows() -> None:
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    lows = [100, 98, 94, 98, 101, 102, 100, 96, 100, 103, 104, 105]
    bars = [
        Bar(
            timestamp=start + timedelta(hours=4 * index),
            open=low + 2,
            high=low + 5,
            low=low,
            close=low + 3,
            volume=1_000,
        )
        for index, low in enumerate(lows)
    ]

    trendline = _confirmed_trendline(bars)

    assert trendline is not None
    _, intercept, slope, kind = trendline
    assert kind == "support"
    assert slope > 0
    assert intercept + slope * 2 == 94
    assert intercept + slope * 7 == 96
