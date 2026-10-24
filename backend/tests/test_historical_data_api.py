from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _archive_bars(count: int = 80) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (start + timedelta(hours=4 * index)).isoformat(),
            "open": 80_000 + index * 20,
            "high": 80_200 + index * 20,
            "low": 79_800 + index * 20,
            "close": 80_100 + index * 20,
            "volume": 1_000 + index,
        }
        for index in range(count)
    ]


def test_binance_archive_import_persists_immutable_dataset(client, monkeypatch):
    import emergenzix_api.historical_data_api as historical_data_api

    monkeypatch.setattr(
        historical_data_api,
        "fetch_binance_btc_4h_archive",
        lambda *, start_at, end_at: _archive_bars(),
    )
    body = {
        "start_at": "2025-01-01T00:00:00Z",
        "end_at": "2025-01-20T00:00:00Z",
    }

    first_response = client.post(
        "/api/v1/datasets/import/binance-btc-4h", json=body
    )
    assert first_response.status_code == 201, first_response.text
    first = first_response.json()
    assert first["source"] == "binance_public_archive"
    assert first["exchange"] == "binance"
    assert first["symbol"] == "BTC/USDT"
    assert first["timeframe"] == "4h"
    assert first["bars_count"] == 80
    assert first["import"]["checksum_verified"] is True
    assert len(first["sha256"]) == 64

    second_response = client.post(
        "/api/v1/datasets/import/binance-btc-4h", json=body
    )
    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["id"] == first["id"]
    assert second_response.json()["sha256"] == first["sha256"]


def test_binance_archive_import_rejects_naive_or_invalid_dates(client):
    naive = client.post(
        "/api/v1/datasets/import/binance-btc-4h",
        json={
            "start_at": "2025-01-01T00:00:00",
            "end_at": "2025-01-20T00:00:00Z",
        },
    )
    assert naive.status_code == 422

    reversed_range = client.post(
        "/api/v1/datasets/import/binance-btc-4h",
        json={
            "start_at": "2025-01-20T00:00:00Z",
            "end_at": "2025-01-01T00:00:00Z",
        },
    )
    assert reversed_range.status_code == 422
