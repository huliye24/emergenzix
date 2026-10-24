from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def _main_args(tmp_path: Path, *, once: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(
        interval=300,
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="4h",
        limit=200,
        lookback=20,
        state_path=tmp_path / "watch.json",
        once=once,
    )


def test_telegram_required_fails_closed_when_credentials_are_missing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from emergenzix_api import watch_agent

    monkeypatch.setattr(watch_agent, "_arguments", lambda _argv=None: _main_args(tmp_path))
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_REQUIRED", "true")
    monkeypatch.setenv(
        "EMERGENZIX_NOTIFICATION_DB_PATH",
        str(tmp_path / "notification-outbox.sqlite3"),
    )
    monkeypatch.delenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("EMERGENZIX_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(watch_agent, "run_once", lambda **_kwargs: pytest.fail("watcher must not run"))

    assert watch_agent.main([]) == 2
    output = capsys.readouterr().out
    assert "MissingTelegramConfiguration" in output
    assert "watcher will not start" in output


def test_telegram_startup_delivery_failure_blocks_watcher(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from emergenzix_api import notifications, watch_agent

    class FailingNotifier:
        def __init__(self, **_kwargs):
            pass

        def send(self, _event, image=None):
            raise notifications.NotificationError("Telegram delivery failed")

    monkeypatch.setattr(watch_agent, "_arguments", lambda _argv=None: _main_args(tmp_path))
    monkeypatch.setattr(notifications, "TelegramNotifier", FailingNotifier)
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_REQUIRED", "true")
    monkeypatch.setenv(
        "EMERGENZIX_NOTIFICATION_DB_PATH",
        str(tmp_path / "notification-outbox.sqlite3"),
    )
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(watch_agent, "run_once", lambda **_kwargs: pytest.fail("watcher must not run"))

    assert watch_agent.main([]) == 2
    output = capsys.readouterr().out
    assert "Telegram startup delivery check failed" in output
    assert "NotificationError" in output
    assert "secret-token" not in output


def test_telegram_startup_success_sends_paper_only_message(
    tmp_path: Path, monkeypatch
) -> None:
    from emergenzix_api import notifications, watch_agent

    sent = []

    class SuccessfulNotifier:
        def __init__(self, **_kwargs):
            pass

        def send(self, event, image=None):
            sent.append(event)

    monkeypatch.setattr(watch_agent, "_arguments", lambda _argv=None: _main_args(tmp_path))
    monkeypatch.setattr(notifications, "TelegramNotifier", SuccessfulNotifier)
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_REQUIRED", "true")
    monkeypatch.setenv(
        "EMERGENZIX_NOTIFICATION_DB_PATH",
        str(tmp_path / "notification-outbox.sqlite3"),
    )
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(watch_agent, "run_once", lambda **_kwargs: [])

    assert watch_agent.main([]) == 0
    assert len(sent) == 1
    assert sent[0].event_type == "watcher_startup"
    assert sent[0].details["live_trading_enabled"] is False
