from __future__ import annotations

from datetime import datetime, timedelta, timezone

from emergenzix_api.schemas import Bar
from emergenzix_api.watch_agent import WatchEvent
from emergenzix_api.watch_chart import render_watch_chart


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
        },
    )

    image = render_watch_chart(event, bars)

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 10_000
