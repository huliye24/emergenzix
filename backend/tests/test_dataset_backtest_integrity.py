"""Dataset integrity must be rechecked before any historical backtest execution."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.dataset_store import verify_stored_dataset


def _registered_inputs(client) -> tuple[str, str]:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "name": "Integrity regression",
            "symbol": "BTCUSDT",
            "strategy_type": "sma_crossover",
            "fast_window": 2,
            "slow_window": 3,
        },
    )
    assert strategy.status_code == 201, strategy.text

    start = datetime(2025, 10, 21, tzinfo=timezone.utc)
    closes = [10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]
    bars = [
        {
            "timestamp": (start + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": 1000.0,
        }
        for index, close in enumerate(closes)
    ]
    dataset = client.post(
        "/api/v1/datasets",
        json={
            "source": "ccxt_public_ohlcv",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "bars": bars,
        },
    )
    assert dataset.status_code == 201, dataset.text
    assert dataset.json()["symbol"] == "BTC/USDT"
    return strategy.json()["id"], dataset.json()["id"]


def _run(client, strategy_id: str, dataset_id: str):
    return client.post(
        "/api/v1/backtests",
        json={"strategy_id": strategy_id, "dataset_id": dataset_id},
    )


def _mutate_column(client, dataset_id: str, field: str, value: object):
    assert field in {
        "bars_json", "bars_count", "content_sha256",
        "source", "symbol", "timeframe", "start_at", "end_at",
    }
    with client.app.state.database.connection() as connection:
        connection.execute(
            f"UPDATE market_datasets SET {field} = ? WHERE id = ?",
            (value, dataset_id),
        )


def test_untouched_registered_dataset_remains_executable_and_verifiable(client):
    strategy_id, dataset_id = _registered_inputs(client)
    with client.app.state.database.connection() as connection:
        row = connection.execute(
            "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,),
        ).fetchone()
        validated = verify_stored_dataset(row)
    assert len(validated) == 15

    response = _run(client, strategy_id, dataset_id)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["data_provenance"]["dataset_id"] == dataset_id
    assert result["data_provenance"]["dataset_sha256"] == dataset_id
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_equivalent_json_layout_is_not_falsely_classified_as_corruption(client):
    strategy_id, dataset_id = _registered_inputs(client)
    with client.app.state.database.connection() as connection:
        row = connection.execute(
            "SELECT bars_json FROM market_datasets WHERE id = ?", (dataset_id,),
        ).fetchone()
    canonical_bars = json.loads(row["bars_json"])
    _mutate_column(client, dataset_id, "bars_json", json.dumps(canonical_bars, indent=2))
    response = _run(client, strategy_id, dataset_id)
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("field,new_value", [
    ("content_sha256", "f" * 64),
    ("bars_count", 100),
    ("source", "synthetic_demo"),
    ("symbol", "ETHUSDT"),
    ("timeframe", "4h"),
    ("start_at", "1900-01-01T00:00:00Z"),
    ("end_at", "1900-01-01T00:00:00Z"),
])
def test_tampered_dataset_metadata_fails_closed_before_engine(
    client, monkeypatch, field: str, new_value: object,
):
    strategy_id, dataset_id = _registered_inputs(client)
    _mutate_column(client, dataset_id, field, new_value)
    import emergenzix_api.main as api_module

    # The engine must never run on data failing the stored fingerprint.
    monkeypatch.setattr(
        api_module, "run_sma_crossover",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("engine called")),
    )
    response = _run(client, strategy_id, dataset_id)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Stored dataset failed integrity check"}
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


@pytest.mark.parametrize("replacement", [
    "{invalid json",
    "null",
    '{"bars":"invalid"}',
    "[1, 2]",
])
def test_malformed_stored_candle_payload_is_rejected_without_debug_leak(
    client, replacement: str,
):
    strategy_id, dataset_id = _registered_inputs(client)
    _mutate_column(client, dataset_id, "bars_json", replacement)
    response = _run(client, strategy_id, dataset_id)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Stored dataset failed integrity check"}
    assert replacement not in response.text
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtests").fetchone()[0] == 0


def test_single_modified_candle_blocked_even_when_ohlcv_still_valid(client):
    strategy_id, dataset_id = _registered_inputs(client)
    with client.app.state.database.connection() as connection:
        stored = connection.execute(
            "SELECT bars_json FROM market_datasets WHERE id = ?", (dataset_id,),
        ).fetchone()
    bars = json.loads(stored["bars_json"])
    bars[8]["close"] += 0.01
    assert bars[8]["low"] < bars[8]["close"] < bars[8]["high"]
    _mutate_column(client, dataset_id, "bars_json", json.dumps(bars))
    response = _run(client, strategy_id, dataset_id)
    assert response.status_code == 409
    assert response.json()["detail"] == "Stored dataset failed integrity check"
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
