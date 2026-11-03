"""Long-running PAPER shadow worker; it never initializes private exchange APIs."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .config import Settings
from .database import Database
from .market_data import fetch_public_ohlcv
from .shadow_runner import list_shadow_sessions, run_shadow_cycle


def run_active_sessions_once(
    db: Database,
    *,
    fetcher: Callable[..., dict[str, Any]] = fetch_public_ohlcv,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Poll every active session once; duplicate completed bars are idempotent."""
    outcomes: list[dict[str, Any]] = []
    for session in list_shadow_sessions(db):
        if session["status"] != "ACTIVE":
            continue
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
                now=now or datetime.now(timezone.utc),
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
            outcomes.append(
                {
                    "session_id": session["id"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
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
    while True:
        for outcome in run_active_sessions_once(db):
            print(json.dumps(outcome, sort_keys=True), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
