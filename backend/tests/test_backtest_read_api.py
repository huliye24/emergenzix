"""Contract and read-only regression tests for historical PAPER backtest retrieval."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from emergenzix_api.main import create_app


def _stored_backtest(client, *, bump: float = 0.0) -> dict:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "name": "Read-only regression",
            "symbol": "BTCUSDT",
            "fast_window": 2,
            "slow_window": 3,
        },
    )
    assert strategy.status_code == 201, strategy.text
    start = datetime(2025, 10, 21, tzinfo=timezone.utc)
    closes = [10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]
    bars = [
        {
            "timestamp": (start + timedelta(days=i)).isoformat(),
            "open": close + bump,
            "high": (close + bump) * 1.02,
            "low": (close + bump) * 0.98,
            "close": close + bump,
            "volume": 1000.0,
        }
        for i, close in enumerate(closes)
    ]
    response = client.post(
        "/api/v1/backtests",
        json={"strategy_id": strategy.json()["id"], "bars": bars},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_saved_backtest_read_routes_keep_public_openapi_shape(client) -> None:
    spec = client.get("/openapi.json").json()
    assert set(spec["paths"]["/api/v1/backtests"]) == {"get", "post"}
    assert set(spec["paths"]["/api/v1/backtests/{backtest_id}"]) == {"get"}
    for item in (
        spec["paths"]["/api/v1/backtests"]["get"],
        spec["paths"]["/api/v1/backtests/{backtest_id}"]["get"],
    ):
        assert "tags" not in item


def test_saved_backtest_list_and_detail_return_exact_immutable_json(client) -> None:
    saved = _stored_backtest(client)
    before = saved.copy()
    identity = saved["id"]

    detail = client.get(f"/api/v1/backtests/{identity}")
    assert detail.status_code == 200
    assert detail.json() == before

    listed = client.get("/api/v1/backtests")
    assert listed.status_code == 200
    assert listed.json() == [before]

    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_saved_backtest_read_validation_and_missing_id_are_unchanged(client) -> None:
    missing = client.get("/api/v1/backtests/nonexistent")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Backtest not found"}

    for query in ("?limit=0", "?limit=101", "?limit=abc"):
        response = client.get("/api/v1/backtests" + query)
        assert response.status_code == 422, response.text
    assert client.get("/api/v1/backtests?limit=1").json() == []


def test_reads_succeed_with_sqlite_database_opened_read_only(client, monkeypatch) -> None:
    saved = _stored_backtest(client)
    db = client.app.state.database
    original_db_path = db.path

    @contextmanager
    def read_only_connection():
        # An actual SQLite read-only connection prevents a hidden write in
        # either GET handler or its dependency chain.
        connection = sqlite3.connect(
            "file:" + original_db_path + "?mode=ro", uri=True, timeout=15.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(db, "connection", read_only_connection)
    listed = client.get("/api/v1/backtests?limit=1")
    detail = client.get(f"/api/v1/backtests/{saved['id']}")
    assert listed.status_code == 200
    assert listed.json() == [saved]
    assert detail.status_code == 200
    assert detail.json() == saved


def test_reads_use_app_local_db_not_process_global_state(client, tmp_path) -> None:
    from fastapi.testclient import TestClient

    saved = _stored_backtest(client)
    separate_app = create_app(
        db_path=tmp_path / "separate.db",
        notification_db_path=tmp_path / "separate-notifications.db",
    )
    with TestClient(separate_app) as independent:
        assert independent.get("/api/v1/backtests").json() == []
        assert independent.get(
            f"/api/v1/backtests/{saved['id']}"
        ).status_code == 404
        assert independent.get("/health").json()["live_trading_enabled"] is False
