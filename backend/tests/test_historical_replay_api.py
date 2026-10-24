from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _bars(count: int = 80) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (start + timedelta(hours=index)).isoformat(),
            "open": 80_000 + index * 20,
            "high": 80_200 + index * 20,
            "low": 79_800 + index * 20,
            "close": 80_100 + index * 20,
            "volume": 1_000 + index,
        }
        for index in range(count)
    ]


def _register_dataset(client, *, symbol: str = "BTC/USDT", count: int = 80):
    response = client.post(
        "/api/v1/datasets",
        json={
            "source": "synthetic_demo",
            "symbol": symbol,
            "timeframe": "1h",
            "bars": _bars(count),
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_btc_replay_record_is_persisted_and_idempotent(client):
    dataset = _register_dataset(client)
    request_body = {
        "dataset_id": dataset["id"],
        "seed": 42,
        "horizon_bars": 12,
        "lookback": 20,
    }

    first_response = client.post(
        "/api/v1/research/historical-replays", json=request_body
    )
    assert first_response.status_code == 201, first_response.text
    first = first_response.json()
    assert first["id"]
    assert first["dataset"]["id"] == dataset["id"]
    assert first["dataset"]["sha256"] == dataset["sha256"]
    assert first["protocol_version"] == "btc-point-in-time-v1"
    assert first["safety"] == {
        "orders_created": False,
        "live_trading_enabled": False,
        "future_visible_during_analysis": False,
        "dataset_mutated": False,
    }
    assert first["experiment"]["safety"]["orders_created"] is False
    assert len(first["record_sha256"]) == 64

    second_response = client.post(
        "/api/v1/research/historical-replays", json=request_body
    )
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert second["id"] == first["id"]
    assert second["record_sha256"] == first["record_sha256"]
    assert second["created_at"] == first["created_at"]

    listed = client.get(
        "/api/v1/research/historical-replays",
        params={"dataset_id": dataset["id"]},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == first["id"]

    detail = client.get(f"/api/v1/research/historical-replays/{first['id']}")
    assert detail.status_code == 200
    assert detail.json() == first

    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connection() as connection:
            connection.execute(
                "UPDATE historical_replay_records SET created_at = ? WHERE id = ?",
                ("2099-01-01T00:00:00+00:00", first["id"]),
            )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connection() as connection:
            connection.execute(
                "DELETE FROM historical_replay_records WHERE id = ?",
                (first["id"],),
            )


def test_btc_replay_rejects_non_btc_dataset(client):
    dataset = _register_dataset(client, symbol="ETH/USDT")
    response = client.post(
        "/api/v1/research/historical-replays",
        json={"dataset_id": dataset["id"], "seed": 7},
    )
    assert response.status_code == 422
    assert "BTC" in response.json()["detail"]


def test_btc_replay_rejects_dataset_that_is_too_short(client):
    dataset = _register_dataset(client, count=30)
    response = client.post(
        "/api/v1/research/historical-replays",
        json={"dataset_id": dataset["id"], "seed": 7},
    )
    assert response.status_code == 422
    assert "too short" in response.json()["detail"]


def test_btc_replay_rejects_tampered_dataset_payload(client):
    dataset = _register_dataset(client)
    database = client.app.state.database
    with database.connection() as connection:
        connection.execute(
            "UPDATE market_datasets SET bars_json = ? WHERE id = ?",
            ("[]", dataset["id"]),
        )

    response = client.post(
        "/api/v1/research/historical-replays",
        json={"dataset_id": dataset["id"], "seed": 7},
    )
    assert response.status_code == 409
    assert "integrity" in response.json()["detail"]
