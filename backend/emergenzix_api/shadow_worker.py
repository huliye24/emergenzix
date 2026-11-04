"""Long-running PAPER shadow worker; it never initializes private exchange APIs."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .config import Settings
from .database import Database
from .market_data import MarketDataUnavailable, fetch_public_ohlcv
from .shadow_runner import (
    ShadowRunnerError,
    list_shadow_sessions,
    record_shadow_cycle_failure,
    run_shadow_cycle,
)
from .short_shadow_runner import list_short_shadow_sessions, run_short_shadow_cycle


def run_active_sessions_once(
    db: Database,
    *,
    fetcher: Callable[..., dict[str, Any]] = fetch_public_ohlcv,
    now: datetime | None = None,
    notification_outbox: Any | None = None,
    notification_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Poll every active session once; duplicate completed bars are idempotent."""
    outcomes: list[dict[str, Any]] = []
    for session in list_shadow_sessions(db):
        if session["status"] != "ACTIVE":
            continue
        observed_at = now or datetime.now(timezone.utc)
        try:
            payload = fetcher(
                exchange_id="binance",
                symbol=session["symbol"],
                timeframe=session["timeframe"],
                limit=200,
            )
            decision = run_shadow_cycle(
                db,
                session_id=session["id"],
                market_payload=payload,
                now=observed_at,
            )
            outcomes.append(
                {
                    "session_id": session["id"],
                    "completed_at": decision["completed_at"],
                    "signal": decision["signal"],
                    "fill_side": decision["fill_side"],
                    "equity": decision["equity"],
                }
            )
        except Exception as exc:
            if isinstance(exc, MarketDataUnavailable):
                error_code = "MARKET_DATA_UNAVAILABLE"
            elif isinstance(exc, ShadowRunnerError):
                error_code = "SHADOW_RUNNER_ERROR"
            elif isinstance(exc, (KeyError, TypeError, ValueError)):
                error_code = "INVALID_MARKET_DATA"
            else:
                error_code = "UNEXPECTED_WORKER_ERROR"
            failure = record_shadow_cycle_failure(
                db,
                session_id=session["id"],
                timeframe=session["timeframe"],
                observed_at=observed_at,
                error_code=error_code,
            )
            notification_id = None
            notification_error_type = None
            if notification_outbox is not None and session["id"] == notification_session_id:
                try:
                    from .shadow_signal_notifications import (
                        enqueue_shadow_failure_notification,
                    )

                    notification_id = enqueue_shadow_failure_notification(
                        notification_outbox, session=session, failure=failure
                    )
                except Exception as notification_exc:
                    notification_error_type = type(notification_exc).__name__
            outcomes.append(
                {
                    "session_id": session["id"],
                    "expected_completed_at": failure["expected_completed_at"],
                    "error_code": failure["error_code"],
                    "failure_record_id": failure["id"],
                    **(
                        {"notification_id": notification_id}
                        if notification_id is not None
                        else {}
                    ),
                    **(
                        {"notification_error_type": notification_error_type}
                        if notification_error_type is not None
                        else {}
                    ),
                }
            )
            continue
        if notification_outbox is not None and session["id"] == notification_session_id:
            try:
                from .shadow_signal_notifications import (
                    enqueue_shadow_decision_notifications,
                )

                notification_ids = enqueue_shadow_decision_notifications(
                    notification_outbox, session=session, decision=decision
                )
                if notification_ids:
                    outcomes[-1]["notification_ids"] = notification_ids
            except Exception as notification_exc:
                outcomes[-1]["notification_error_type"] = type(
                    notification_exc
                ).__name__
    return outcomes


def run_active_short_sessions_once(
    db: Database,
    *,
    fetcher: Callable[..., dict[str, Any]] = fetch_public_ohlcv,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Poll isolated short-only PAPER sessions using public candles only."""
    outcomes: list[dict[str, Any]] = []
    for session in list_short_shadow_sessions(db):
        if session["status"] != "ACTIVE":
            continue
        try:
            payload = fetcher(
                exchange_id="binance",
                symbol=session["symbol"],
                timeframe=session["timeframe"],
                limit=200,
            )
            decision = run_short_shadow_cycle(
                db,
                session_id=session["id"],
                market_payload=payload,
                now=now or datetime.now(timezone.utc),
            )
            outcomes.append(
                {
                    "session_id": session["id"],
                    "fleet_id": session["fleet_id"],
                    "strategy_key": session["strategy_key"],
                    "completed_at": decision["completed_at"],
                    "signal": decision["signal"],
                    "fill_side": decision["fill_side"],
                    "equity": decision["equity"],
                    "execution_mode": "PAPER_ONLY",
                }
            )
        except Exception as exc:
            outcomes.append(
                {
                    "session_id": session["id"],
                    "fleet_id": session["fleet_id"],
                    "strategy_key": session["strategy_key"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "execution_mode": "PAPER_ONLY",
                }
            )
    return outcomes


def main() -> None:
    settings = Settings.from_env()
    db = Database(
        str(settings.db_path),
        initial_paper_cash=settings.initial_paper_cash,
        seed_builtin_strategies=settings.seed_builtin_strategies,
    )
    interval = float(os.getenv("EMERGENZIX_SHADOW_INTERVAL_SECONDS", "30"))
    if interval < 5:
        raise ValueError("EMERGENZIX_SHADOW_INTERVAL_SECONDS must be at least 5")
    follow_enabled = os.getenv(
        "EMERGENZIX_FOLLOW_TELEGRAM_ENABLED", "false"
    ).strip().lower()
    if follow_enabled not in {"true", "false"}:
        raise ValueError("EMERGENZIX_FOLLOW_TELEGRAM_ENABLED must be true or false")
    outbox = None
    notifier = None
    follow_session_id = None
    if follow_enabled == "true":
        from .notification_outbox import NotificationOutbox
        from .notifications import TelegramNotifier
        from .telegram_credentials import load_follow_telegram_bot_token

        token = load_follow_telegram_bot_token()
        chat_id = os.getenv("EMERGENZIX_FOLLOW_TELEGRAM_CHAT_ID", "").strip()
        follow_session_id = os.getenv("EMERGENZIX_FOLLOW_SESSION_ID", "").strip()
        if not token or not chat_id or not follow_session_id:
            raise ValueError("follow Telegram configuration is incomplete")
        outbox = NotificationOutbox(
            os.getenv(
                "EMERGENZIX_FOLLOW_NOTIFICATION_DB_PATH",
                "/data/follow-notification-outbox.sqlite3",
            )
        )
        notifier = TelegramNotifier(bot_token=token, chat_id=chat_id)
    while True:
        for outcome in run_active_sessions_once(
            db,
            notification_outbox=outbox,
            notification_session_id=follow_session_id,
        ):
            print(json.dumps(outcome, sort_keys=True), flush=True)
        for outcome in run_active_short_sessions_once(db):
            print(json.dumps(outcome, sort_keys=True), flush=True)
        if outbox is not None and notifier is not None:
            for result in outbox.deliver_pending(notifier):
                print(
                    json.dumps(
                        {
                            "notification_id": result.notification_id,
                            "notification_status": result.status,
                            "attempts": result.attempts,
                            "error_type": result.error_type,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
        time.sleep(interval)


if __name__ == "__main__":
    main()
