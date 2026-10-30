"""Durable PAPER execution alerts routed through the existing outbox."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .notification_outbox import NotificationOutbox
from .watch_agent import WatchEvent


def enqueue_paper_execution_alert(
    outbox: NotificationOutbox,
    *,
    alert_key: str,
    condition: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> str:
    if not alert_key.strip() or not condition.strip() or not message.strip():
        raise ValueError("alert_key, condition, and message are required")
    safe_details = dict(details or {})
    safe_details.update({"alert_key": alert_key, "condition": condition})
    event = WatchEvent(
        event_type="paper_execution_alert",
        severity="error",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        exchange="paper",
        symbol=str(safe_details.pop("symbol", "ACCOUNT")),
        timeframe="operational",
        message=message,
        details=safe_details,
    )
    return outbox.enqueue(event)
