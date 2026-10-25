"""Operational routes remain versioned, read-only and fail closed after extraction."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from emergenzix_api import __version__
from emergenzix_api import operational_status_api


def test_operational_paths_registered_once_and_preserve_contract(client) -> None:
    paths = [route.path for route in client.app.routes if hasattr(route, "path")]
    for path in ("/health", "/ready", "/api/v1/notifications/status"):
        assert paths.count(path) == 1

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "emergenzix-api",
        "version": __version__,
        "mode": "PAPER",
        "live_trading_enabled": False,
    }
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {
        "database": "ok",
        "paper_account": "ok",
    }
    assert ready.json()["mode"] == "PAPER"
    assert ready.json()["live_trading_enabled"] is False


def test_notification_status_masks_sqlite_internal_error(
    client, monkeypatch,
) -> None:
    outbox = Path(client.app.state.notification_outbox_path)
    outbox.touch()
    secret = "fake-telegram-secret-not-for-output"

    class BrokenOutbox:
        def __init__(self, path):
            assert Path(path) == outbox

        def summary(self):
            raise sqlite3.OperationalError(
                "database locked with token=" + secret
            )

    monkeypatch.setattr(operational_status_api, "NotificationOutbox", BrokenOutbox)
    response = client.get("/api/v1/notifications/status")
    assert response.status_code == 503
    assert response.json() == {"detail": "notification outbox is unavailable"}
    assert secret not in response.text
    assert "database locked" not in response.text


def test_notification_status_does_not_create_missing_sqlite_database(
    client,
) -> None:
    outbox = Path(client.app.state.notification_outbox_path)
    assert not outbox.exists()
    response = client.get("/api/v1/notifications/status")
    assert response.status_code == 200
    assert response.json()["status"] == "not_initialized"
    assert response.json()["metrics"]["pending"] == 0
    assert response.json()["live_trading_enabled"] is False
    assert not outbox.exists()
