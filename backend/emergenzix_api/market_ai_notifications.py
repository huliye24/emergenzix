"""Durably queue verified market AI advisories for Telegram delivery."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .notification_outbox import NotificationOutbox
from .watch_agent import WatchEvent


def queue_market_ai_advisory(
    outbox_path: str | Path,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Queue one advisory per symbol/timeframe/completed candle."""
    advisory = snapshot.get("ai_analysis")
    if not isinstance(advisory, Mapping) or advisory.get("status") != "verified_advisory":
        return {"status": "not_queued", "reason": "verified_advisory_unavailable"}
    analysis = advisory.get("analysis")
    freshness = snapshot.get("freshness")
    if not isinstance(analysis, Mapping) or not isinstance(freshness, Mapping):
        return {"status": "not_queued", "reason": "invalid_advisory"}
    completed_at = str(freshness.get("latest_completed_at", "")).strip()
    symbol = str(snapshot.get("symbol", "")).strip()
    timeframe = str(snapshot.get("timeframe", "")).strip()
    summary = str(analysis.get("summary", "")).strip()
    if not completed_at or not symbol or not timeframe or not summary:
        return {"status": "not_queued", "reason": "invalid_advisory"}

    risks = analysis.get("risks")
    risk_items = [str(item) for item in risks[:3]] if isinstance(risks, list) else []
    model = str(advisory.get("model", "DeepSeek"))
    event = WatchEvent(
        event_type="ai_market_analysis",
        severity="info",
        occurred_at=str(snapshot.get("observed_at", completed_at)),
        exchange=str(snapshot.get("exchange", "binance")),
        symbol=symbol,
        timeframe=timeframe,
        message="Verified DeepSeek market advisory is available.",
        details={
            "alert_key": f"ai-market:{symbol}:{timeframe}:{completed_at}",
            "last_completed_at": completed_at,
            "model": model,
            "summary": summary,
            "risks": risk_items,
            "advisory_only": True,
            "paper_order_permission": False,
            "live_order_permission": False,
        },
    )
    notification_id = NotificationOutbox(outbox_path).enqueue(event)
    return {"status": "queued", "notification_id": notification_id}
