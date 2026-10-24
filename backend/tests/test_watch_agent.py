from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from emergenzix_api.watch_agent import WatchState, evaluate_market_payload, run_once


def _payload(closes: list[float], now: datetime) -> dict[str, object]:
    start = now - timedelta(hours=4 * len(closes))
    bars = [{"timestamp": (start + timedelta(hours=4 * i)).isoformat(), "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1_000} for i, close in enumerate(closes)]
    return {"exchange": "binance", "symbol": "BTC/USDT", "timeframe": "4h", "bars": bars}


def test_first_snapshot_emits_once_and_discards_forming_bar() -> None:
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    payload = _payload([100 + (index % 3) for index in range(31)], now)
    first_state, first_events = evaluate_market_payload(payload, WatchState(), lookback=20, now=now)
    assert [event.event_type for event in first_events] == ["initial_snapshot"]
    assert first_state.last_completed_at == payload["bars"][-2]["timestamp"]
    second_state, second_events = evaluate_market_payload(payload, first_state, lookback=20, now=now)
    assert second_state == first_state
    assert second_events == []


def test_new_confirmed_breakdown_emits_transition_and_trigger() -> None:
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    payload = _payload([100 + (i % 3) for i in range(27)] + [98, 96, 94, 93], now)
    _, events = evaluate_market_payload(payload, WatchState(scenario="range", last_completed_at="earlier"), lookback=20, now=now)
    assert [event.event_type for event in events] == ["scenario_changed", "trade_trigger"]
    assert events[0].details["scenario"] == "continuation_down"


def test_stale_data_blocks_analysis_and_deduplicates_warning() -> None:
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    payload = _payload([100.0] * 31, now - timedelta(days=3))
    state, events = evaluate_market_payload(payload, WatchState(), lookback=20, now=now)
    assert [event.event_type for event in events] == ["risk_alert"]
    _, repeated = evaluate_market_payload(payload, state, lookback=20, now=now)
    assert repeated == []


def test_run_once_persists_and_suppresses_duplicate_events(tmp_path: Path) -> None:
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    payload = _payload([100 + (i % 3) for i in range(31)], now)
    def fetcher(**_kwargs):
        return payload
    state_path = tmp_path / "watch.json"
    assert run_once(exchange="binance", symbol="BTCUSDT", timeframe="4h", limit=200, lookback=20, state_path=state_path, fetcher=fetcher, now=now)
    assert state_path.exists()
    assert run_once(exchange="binance", symbol="BTCUSDT", timeframe="4h", limit=200, lookback=20, state_path=state_path, fetcher=fetcher, now=now) == []
