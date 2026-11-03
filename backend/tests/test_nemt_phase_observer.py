from __future__ import annotations

from datetime import datetime, timedelta, timezone

from emergenzix_api.nemt_phase_observer import observe_nemt_phase
from emergenzix_api.schemas import Bar


def _bars(closes: list[float]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(timestamp=start + timedelta(hours=index), open=close, high=close + 1,
            low=close - 1, close=close, volume=1_000)
        for index, close in enumerate(closes)
    ]


def test_nemt_price_observer_identifies_bullish_trend_without_order_permission() -> None:
    result = observe_nemt_phase(_bars([100 + index for index in range(30)]))
    assert result["phase"] == "D_TREND"
    assert result["direction"] == "BULLISH"
    assert result["research_signal"] == "WATCH_LONG"
    assert result["order_permission"] is False
    assert result["unavailable_phases"] == ["B_VORTEX", "C_RESONANCE"]


def test_nemt_price_observer_fails_to_no_action_in_noise() -> None:
    result = observe_nemt_phase(_bars([100 + index % 2 for index in range(30)]))
    assert result["phase"] == "A_NOISE_OR_TRANSITION"
    assert result["research_signal"] == "NO_ACTION"
    assert result["strategy_candidate"] is None
