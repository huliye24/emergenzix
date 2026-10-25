"""Private operational health, readiness, and notification status endpoints.

Only report service state; no trading, research execution, credentials or
notification event bodies. Service state is obtained from request.app.state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import __version__
from .database import Database
from .notification_outbox import NotificationOutbox

router = APIRouter()

@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "emergenzix-api",
        "version": __version__,
        "mode": "PAPER",
        "live_trading_enabled": False,
    }

@router.get("/ready")
def readiness(request: Request) -> dict[str, Any]:
    """Report readiness only when the database and paper account are usable."""
    db: Database = request.app.state.database
    try:
        with db.connection() as connection:
            connection.execute("SELECT 1").fetchone()
            paper_account = connection.execute(
                "SELECT id FROM paper_account WHERE id = 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503, detail="database is unavailable"
        ) from exc

    if paper_account is None:
        raise HTTPException(
            status_code=503, detail="paper account is not initialized"
        )

    return {
        "status": "ready",
        "service": "emergenzix-api",
        "version": __version__,
        "mode": "PAPER",
        "live_trading_enabled": False,
        "checks": {
            "database": "ok",
            "paper_account": "ok",
        },
    }

@router.get("/api/v1/notifications/status")
def notification_status(request: Request) -> dict[str, Any]:
    """Expose aggregate outbox health without event bodies or Telegram credentials."""
    path = Path(request.app.state.notification_outbox_path)
    empty_metrics = {
        "total": 0,
        "pending": 0,
        "retrying": 0,
        "due": 0,
        "failed_attempts": 0,
        "delivered": 0,
        "dead": 0,
        "oldest_pending_at": None,
        "last_successful_delivery_at": None,
        "last_error_type": None,
    }
    if not path.exists():
        return {
            "status": "not_initialized",
            "service": "emergenzix-notification-outbox",
            "mode": "PAPER",
            "live_trading_enabled": False,
            "metrics": empty_metrics,
        }

    try:
        metrics = NotificationOutbox(path).summary()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="notification outbox is unavailable"
        ) from exc

    return {
        "status": "attention_required" if metrics["dead"] else "ok",
        "service": "emergenzix-notification-outbox",
        "mode": "PAPER",
        "live_trading_enabled": False,
        "metrics": metrics,
    }
