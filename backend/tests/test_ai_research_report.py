from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from emergenzix_api.ai_research_report import run_ai_research_report
from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.notifications import format_watch_event
from emergenzix_api.watch_agent import WatchEvent


def _market_payload():
    start = datetime(2026, 10, 18, 16, tzinfo=timezone.utc)
    bars = []
    for index in range(80):
        close = 70_000 + index * 50
        bars.append(
            {
                "timestamp": (start + timedelta(hours=4 * index)).isoformat(),
                "open": close - 20,
                "high": close + 100,
                "low": close - 100,
                "close": close,
                "volume": 100 + index,
            }
        )
    return {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "4h",
        "bars": bars,
    }


def _analyzer(**_kwargs):
    return {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "analysis": {
            "summary": "Trend evidence is positive but incomplete.",
            "observations": ["Completed prices increased."],
            "risks": ["Short sample."],
            "data_quality": ["Completed candles only."],
            "limitations": ["No profitability claim."],
            "recommended_research": ["Continue PAPER observation."],
        },
        "advisory_only": True,
        "automatic_strategy_promotion": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
    }


def test_report_is_persisted_and_queued_once_per_completed_candle(tmp_path):
    outbox_path = tmp_path / "outbox.sqlite3"
    outbox = NotificationOutbox(outbox_path)
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    kwargs = {
        "output_dir": tmp_path / "reports",
        "outbox": outbox,
        "market_fetcher": lambda **_: _market_payload(),
        "analyzer": _analyzer,
        "now": now,
    }

    first = run_ai_research_report(**kwargs)
    second = run_ai_research_report(**kwargs)

    assert first["report_id"] == second["report_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert outbox.pending_count() == 1
    report = json.loads((tmp_path / "reports" / f"{first['report_id']}.json").read_text())
    assert report["advisory_only"] is True
    assert report["paper_order_permission"] is False
    assert report["testnet_order_permission"] is False
    assert report["live_order_permission"] is False


def test_report_notification_is_explicitly_advisory_only(tmp_path):
    outbox_path = tmp_path / "outbox.sqlite3"
    result = run_ai_research_report(
        output_dir=tmp_path / "reports",
        outbox=NotificationOutbox(outbox_path),
        market_fetcher=lambda **_: _market_payload(),
        analyzer=_analyzer,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    connection = sqlite3.connect(outbox_path)
    try:
        payload = connection.execute(
            "SELECT event_json FROM notification_outbox WHERE id = ?",
            (result["notification_id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    message = format_watch_event(WatchEvent(**json.loads(payload)))
    assert "DeepSeek 六小时研究报告" in message
    assert "不能修改策略" in message
    assert "PAPER/TESTNET/LIVE" in message
    assert "不构成投资建议" in message
