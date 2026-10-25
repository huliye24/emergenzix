"""Regression coverage for immutable dataset endpoints after router extraction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _bars() -> list[dict[str, object]]:
    start = datetime(2025, 10, 21, tzinfo=timezone.utc)
    bars = []
    for index, price in enumerate((100, 101, 103, 102, 105, 106)):
        timestamp = start + timedelta(hours=index)
        bars.append({
            "timestamp": timestamp.isoformat(),
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000 + index,
        })
    return bars


def test_dataset_openapi_routes_and_methods_unchanged(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/datasets": {"get", "post"},
        "/api/v1/datasets/{dataset_id}": {"get"},
        "/api/v1/datasets/{dataset_id}/quality": {"get"},
    }
    for path, methods in expected.items():
        assert set(paths[path]) == methods
        for method in methods:
            assert "tags" not in paths[path][method]


def test_dataset_create_list_get_and_idempotent_content_sha(client) -> None:
    payload = {"symbol": "BTCUSDT", "timeframe": "1h", "bars": _bars()}
    first = client.post("/api/v1/datasets", json=payload)
    assert first.status_code == 201, first.text
    created = first.json()
    assert len(created["id"]) == 64
    assert len(created["sha256"]) == 64
    assert created["bars_count"] == 6
    assert "bars" not in created

    duplicate = client.post("/api/v1/datasets", json=payload)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json() == created

    listed = client.get("/api/v1/datasets?limit=1")
    assert listed.status_code == 200
    assert listed.json() == [created]
    assert "bars" not in listed.text

    detail = client.get("/api/v1/datasets/" + created["id"])
    assert detail.status_code == 200, detail.text
    result = detail.json()
    assert {key: value for key, value in result.items() if key != "bars"} == created
    assert len(result["bars"]) == 6
    assert result["bars"][0]["timestamp"] == payload["bars"][0]["timestamp"]
    assert result["bars"][-1]["close"] == payload["bars"][-1]["close"]

    quality = client.get("/api/v1/datasets/" + created["id"] + "/quality")
    assert quality.status_code == 200, quality.text
    assert quality.json()["sha256"] == created["sha256"]
    assert quality.json()["bars_count"] == 6
    with client.app.state.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_datasets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_dataset_reads_not_found_without_implicit_mutation(client) -> None:
    missing = "f" * 64
    for endpoint in (
        "/api/v1/datasets/" + missing,
        "/api/v1/datasets/" + missing + "/quality",
    ):
        response = client.get(endpoint)
        assert response.status_code == 404
        assert response.json() == {"detail": "Dataset not found"}
    with client.app.state.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_datasets").fetchone()[0] == 0


def test_invalid_dataset_bars_rejected_before_storage(client) -> None:
    bars = _bars()
    bars[3]["timestamp"] = bars[2]["timestamp"]
    invalid = client.post(
        "/api/v1/datasets",
        json={"symbol": "BTCUSDT", "timeframe": "1h", "bars": bars},
    )
    assert invalid.status_code == 422
    with client.app.state.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_datasets").fetchone()[0] == 0


def test_ccxt_public_dataset_symbol_normalization_unchanged(client) -> None:
    payload = {
        "source": "ccxt_public_ohlcv",
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "bars": _bars(),
    }
    created = client.post("/api/v1/datasets", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["symbol"] == "BTC/USDT"
    assert created.json()["source"] == "ccxt_public_ohlcv"
    assert created.json()["exchange"] == "binance"
