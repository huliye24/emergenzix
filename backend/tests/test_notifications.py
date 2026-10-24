from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from emergenzix_api.notifications import NotificationError, TelegramNotifier
from emergenzix_api.watch_agent import WatchEvent


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _event() -> WatchEvent:
    return WatchEvent(
        event_type="scenario_changed",
        severity="warning",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="4h",
        message="Scenario changed from range to continuation_down.",
        details={
            "scenario": "continuation_down",
            "key_levels": {"support": 80_000, "resistance": 85_000, "atr": 750},
            "trigger": "two completed closes below support",
            "invalidation": "completed close back above support",
        },
    )


def test_telegram_notifier_posts_expected_message_without_orders() -> None:
    captured = {}

    def requester(request, *, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": {"message_id": 1}})

    notifier = TelegramNotifier("secret-token", "12345", requester=requester)
    notifier.send(_event())
    assert captured["url"].endswith("/sendMessage")
    assert captured["body"]["chat_id"] == "12345"
    assert "场景发生切换" in captured["body"]["text"]
    assert "当前场景：延续下跌" in captured["body"]["text"]
    assert "关键位置：支撑=80000" in captured["body"]["text"]
    assert "触发条件：连续两根已收盘K线跌破 支撑位" in captured["body"]["text"]
    assert "失效条件：已收盘K线重新站上 支撑位" in captured["body"]["text"]
    assert "不会创建任何订单" in captured["body"]["text"]


def test_telegram_rejection_is_sanitized() -> None:
    def requester(_request, *, timeout):
        return FakeResponse({"ok": False, "description": "secret-token invalid"})

    notifier = TelegramNotifier("secret-token", "12345", requester=requester)
    with pytest.raises(NotificationError) as error:
        notifier.send(_event())
    assert "secret-token" not in str(error.value)


def test_telegram_notifier_sends_chart_as_photo() -> None:
    captured = {}

    def requester(request, *, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["content_type"] = request.headers["Content-type"]
        return FakeResponse({"ok": True, "result": {"message_id": 2}})

    TelegramNotifier("secret-token", "12345", requester=requester).send(_event(), image=b"\x89PNG-test")
    assert captured["url"].endswith("/sendPhoto")
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert b'filename="emergenzix.png"' in captured["body"]
    assert b"\x89PNG-test" in captured["body"]
