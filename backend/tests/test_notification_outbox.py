from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.watch_agent import WatchEvent


def _event() -> WatchEvent:
    return WatchEvent(
        event_type="scenario_changed",
        severity="warning",
        occurred_at="2026-10-24T04:00:00+00:00",
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="4h",
        message="Scenario changed to continuation_down.",
        details={"scenario": "continuation_down", "bar_timestamp": "2026-10-24T00:00:00+00:00"},
    )


class RecordingNotifier:
    def __init__(self):
        self.events = []
        self.images = []

    def send(self, event, image=None):
        self.events.append(event)
        self.images.append(image)


class FailingNotifier:
    def send(self, event, image=None):
        raise RuntimeError("secret provider response must not be persisted")


def test_outbox_deduplicates_same_candle_across_restarts(tmp_path: Path) -> None:
    path = tmp_path / "outbox.sqlite3"
    first = NotificationOutbox(path)
    event = _event()
    first_id = first.enqueue(event, image=b"png-data")

    restarted = NotificationOutbox(path)
    second_id = restarted.enqueue(replace(event, occurred_at="2026-10-24T04:05:00+00:00"))

    assert second_id == first_id
    assert restarted.pending_count() == 1


def test_failed_delivery_survives_restart_and_retries_after_backoff(tmp_path: Path) -> None:
    path = tmp_path / "outbox.sqlite3"
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    outbox = NotificationOutbox(path, retry_base_seconds=30, retry_max_seconds=60)
    notification_id = outbox.enqueue(_event(), image=b"chart")

    failed = outbox.deliver_one(FailingNotifier(), notification_id, now=now, force=True)
    assert failed.status == "pending"
    assert failed.attempts == 1
    assert failed.error_type == "RuntimeError"

    restarted = NotificationOutbox(path, retry_base_seconds=30, retry_max_seconds=60)
    notifier = RecordingNotifier()
    too_early = restarted.deliver_pending(notifier, now=now + timedelta(seconds=29))
    assert too_early == []
    delivered = restarted.deliver_pending(notifier, now=now + timedelta(seconds=30))

    assert [item.status for item in delivered] == ["delivered"]
    assert notifier.events[0].event_type == "scenario_changed"
    assert notifier.images == [b"chart"]
    assert restarted.pending_count() == 0
    assert restarted.get_status(notification_id).status == "delivered"
    assert restarted.summary()["failed_attempts"] == 1


def test_dead_letter_is_retained_after_maximum_attempts(tmp_path: Path) -> None:
    path = tmp_path / "outbox.sqlite3"
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    outbox = NotificationOutbox(
        path, retry_base_seconds=1, retry_max_seconds=2, max_attempts=2
    )
    notification_id = outbox.enqueue(_event())

    first = outbox.deliver_one(FailingNotifier(), notification_id, now=now, force=True)
    second = outbox.deliver_one(
        FailingNotifier(), notification_id, now=now + timedelta(seconds=1), force=True
    )

    assert first.status == "pending"
    assert second.status == "dead"
    assert second.attempts == 2
    assert outbox.pending_count() == 0
    assert outbox.get_status(notification_id).status == "dead"


def test_delivered_notification_is_not_sent_twice(tmp_path: Path) -> None:
    outbox = NotificationOutbox(tmp_path / "outbox.sqlite3")
    notification_id = outbox.enqueue(_event())
    notifier = RecordingNotifier()

    first = outbox.deliver_one(
        notifier, notification_id, now=datetime(2026, 10, 24, 4, tzinfo=timezone.utc), force=True
    )
    second = outbox.deliver_one(
        notifier, notification_id, now=datetime(2026, 10, 24, 4, tzinfo=timezone.utc), force=True
    )

    assert first.status == "delivered"
    assert second.status == "delivered"
    assert len(notifier.events) == 1


def test_summary_reports_retrying_due_dead_and_last_success(tmp_path: Path) -> None:
    now = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    outbox = NotificationOutbox(
        tmp_path / "outbox.sqlite3",
        retry_base_seconds=30,
        retry_max_seconds=60,
        max_attempts=2,
    )
    failed_id = outbox.enqueue(_event())
    failed = outbox.deliver_one(FailingNotifier(), failed_id, now=now, force=True)
    assert failed.status == "pending"

    distinct_event = replace(
        _event(),
        message="A second event was delivered.",
        details={"scenario": "reversal_up", "bar_timestamp": "2026-10-24T04:00:00+00:00"},
    )
    delivered_id = outbox.enqueue(distinct_event)
    delivered = outbox.deliver_one(
        RecordingNotifier(), delivered_id, now=now, force=True
    )
    assert delivered.status == "delivered"

    summary = outbox.summary(now=now + timedelta(seconds=31))
    assert summary["total"] == 2
    assert summary["pending"] == 1
    assert summary["retrying"] == 1
    assert summary["due"] == 1
    assert summary["failed_attempts"] == 1
    assert summary["delivered"] == 1
    assert summary["dead"] == 0
    assert summary["last_successful_delivery_at"] == now.isoformat()
    assert summary["last_error_type"] == "RuntimeError"
