"""Convert persisted shadow evidence into idempotent PAPER follow events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .notification_outbox import NotificationOutbox
from .watch_agent import WatchEvent


def enqueue_shadow_decision_notifications(
    outbox: NotificationOutbox,
    *,
    session: dict[str, Any],
    decision: dict[str, Any],
) -> list[str]:
    """Queue signal and fill events only after their decision is persisted."""
    notification_ids: list[str] = []
    common = {
        "session_id": session["id"],
        "strategy_version_id": session["strategy_version_id"],
        "strategy_sha256": session["strategy_sha256"],
        "completed_at": decision["completed_at"],
        "mode": "PAPER",
        "validation_status": "UNVERIFIED_30_DAY",
        "live_order_permission": False,
    }
    occurred_at = str(decision.get("created_at") or datetime.now(timezone.utc).isoformat())
    signal = str(decision["signal"])
    if signal != "HOLD":
        details = {
            **common,
            "alert_key": (
                f"shadow:{session['id']}:{decision['completed_at']}:signal"
            ),
            "signal": signal,
            "reason": decision["reason"],
            "reference_close": decision["close_price"],
            "execution_assumption": "next completed candle open",
            "fee_bps": session["fee_bps"],
            "slippage_bps": session["slippage_bps"],
        }
        notification_ids.append(
            outbox.enqueue(
                WatchEvent(
                    event_type="shadow_signal",
                    severity="warning",
                    occurred_at=occurred_at,
                    exchange="binance",
                    symbol=session["symbol"],
                    timeframe=session["timeframe"],
                    message=f"PAPER {signal} signal recorded.",
                    details=details,
                )
            )
        )
    if decision.get("fill_side"):
        details = {
            **common,
            "alert_key": (
                f"shadow:{session['id']}:{decision['completed_at']}:fill"
            ),
            "fill_side": decision["fill_side"],
            "fill_quantity": decision["fill_quantity"],
            "fill_price": decision["fill_price"],
            "fee": decision["fee"],
            "cash": decision["cash"],
            "position_quantity": decision["position_quantity"],
            "equity": decision["equity"],
        }
        notification_ids.append(
            outbox.enqueue(
                WatchEvent(
                    event_type="shadow_fill",
                    severity="info",
                    occurred_at=occurred_at,
                    exchange="binance",
                    symbol=session["symbol"],
                    timeframe=session["timeframe"],
                    message="PAPER simulated fill recorded.",
                    details=details,
                )
            )
        )
    return notification_ids


def enqueue_shadow_failure_notification(
    outbox: NotificationOutbox,
    *,
    session: dict[str, Any],
    failure: dict[str, Any],
) -> str:
    """Queue a sanitized data/runtime failure without provider error text."""
    completed_at = failure["expected_completed_at"]
    return outbox.enqueue(
        WatchEvent(
            event_type="shadow_data_failure",
            severity="error",
            occurred_at=failure["created_at"],
            exchange="binance",
            symbol=session["symbol"],
            timeframe=session["timeframe"],
            message="PAPER strategy paused for this expected candle.",
            details={
                "alert_key": f"shadow:{session['id']}:{completed_at}:failure",
                "session_id": session["id"],
                "strategy_version_id": session["strategy_version_id"],
                "expected_completed_at": completed_at,
                "error_code": failure["error_code"],
                "mode": "PAPER",
                "validation_status": "UNVERIFIED_30_DAY",
                "live_order_permission": False,
            },
        )
    )
