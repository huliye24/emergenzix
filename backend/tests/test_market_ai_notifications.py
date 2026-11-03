from __future__ import annotations

import json

from emergenzix_api.market_ai_notifications import queue_market_ai_advisory
from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.notifications import format_watch_event
from emergenzix_api.watch_agent import WatchEvent


def _snapshot(summary: str = "Downtrend evidence remains mixed.") -> dict:
    return {
        "exchange": "binance",
        "symbol": "SOL/USDT",
        "timeframe": "4h",
        "observed_at": "2026-11-03T12:30:00+00:00",
        "freshness": {"latest_completed_at": "2026-11-03T12:00:00+00:00"},
        "ai_analysis": {
            "status": "verified_advisory",
            "model": "deepseek-flash",
            "analysis": {
                "summary": summary,
                "risks": ["A reversal can invalidate the trend.", "Volume is weak."],
            },
        },
    }


def test_verified_advisory_is_queued_once_per_completed_candle(tmp_path) -> None:
    path = tmp_path / "outbox.sqlite3"
    first = queue_market_ai_advisory(path, _snapshot("First wording."))
    second = queue_market_ai_advisory(path, _snapshot("Different retry wording."))

    assert first["status"] == "queued"
    assert second["notification_id"] == first["notification_id"]
    outbox = NotificationOutbox(path)
    assert outbox.summary()["pending"] == 1


def test_unverified_advisory_is_not_queued(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot["ai_analysis"] = {"status": "unavailable"}

    result = queue_market_ai_advisory(tmp_path / "outbox.sqlite3", snapshot)

    assert result == {
        "status": "not_queued",
        "reason": "verified_advisory_unavailable",
    }


def test_telegram_message_is_compact_and_never_authorizes_orders(tmp_path) -> None:
    path = tmp_path / "outbox.sqlite3"
    queued = queue_market_ai_advisory(path, _snapshot())
    outbox = NotificationOutbox(path)
    with outbox._connection() as connection:
        row = connection.execute(
            "SELECT event_json FROM notification_outbox WHERE id = ?",
            (queued["notification_id"],),
        ).fetchone()
    event = WatchEvent(**json.loads(row["event_json"]))
    message = format_watch_event(event)

    assert "AI 市场分析" in message
    assert "deepseek-flash" in message
    assert "SOL/USDT · 4h" in message
    assert "不会创建任何订单" in message
    assert "API" not in message
