from __future__ import annotations

import json
import sqlite3

from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.notifications import format_watch_event
from emergenzix_api.shadow_signal_notifications import (
    enqueue_shadow_decision_notifications,
    enqueue_shadow_failure_notification,
)
from emergenzix_api.watch_agent import WatchEvent


SESSION = {
    "id": "session-1",
    "strategy_version_id": "version-1",
    "strategy_sha256": "a" * 64,
    "symbol": "BTCUSDT",
    "timeframe": "4h",
    "fee_bps": 10.0,
    "slippage_bps": 5.0,
}


def _decision(**changes):
    result = {
        "completed_at": "2026-11-04T00:00:00+00:00",
        "created_at": "2026-11-04T04:00:05+00:00",
        "signal": "ENTER",
        "reason": "close above prior 20-bar entry channel",
        "fill_side": None,
        "fill_quantity": 0.0,
        "fill_price": None,
        "fee": 0.0,
        "close_price": 101000.0,
        "cash": 10000.0,
        "position_quantity": 0.0,
        "equity": 10000.0,
    }
    result.update(changes)
    return result


def _events(path):
    connection = sqlite3.connect(path)
    try:
        return [
            WatchEvent(**json.loads(row[0]))
            for row in connection.execute(
                "SELECT event_json FROM notification_outbox ORDER BY created_at, id"
            )
        ]
    finally:
        connection.close()


def test_signal_is_idempotent_and_hold_does_not_enqueue(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    outbox = NotificationOutbox(path)

    first = enqueue_shadow_decision_notifications(
        outbox, session=SESSION, decision=_decision()
    )
    second = enqueue_shadow_decision_notifications(
        outbox, session=SESSION, decision=_decision()
    )
    hold = enqueue_shadow_decision_notifications(
        outbox, session=SESSION, decision=_decision(signal="HOLD")
    )

    assert first == second
    assert hold == []
    assert outbox.pending_count() == 1
    message = format_watch_event(_events(path)[0])
    assert "PAPER 策略信号" in message
    assert "下一根已完成K线开盘模拟成交" in message
    assert "尚未完成30天观察" in message
    assert "非投资建议" in message


def test_fill_and_failure_have_separate_stable_identities(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    outbox = NotificationOutbox(path)
    ids = enqueue_shadow_decision_notifications(
        outbox,
        session=SESSION,
        decision=_decision(
            fill_side="BUY", fill_quantity=0.01, fill_price=101050.5, fee=1.01
        ),
    )
    failure_id = enqueue_shadow_failure_notification(
        outbox,
        session=SESSION,
        failure={
            "expected_completed_at": "2026-11-04T04:00:00+00:00",
            "created_at": "2026-11-04T08:00:05+00:00",
            "error_code": "MARKET_DATA_UNAVAILABLE",
        },
    )

    assert len(set([*ids, failure_id])) == 3
    assert outbox.pending_count() == 3
    messages = [format_watch_event(event) for event in _events(path)]
    assert any("PAPER 模拟成交" in message for message in messages)
    assert any("PAPER 数据失败" in message for message in messages)
    assert all("live" not in message.lower() for message in messages)
