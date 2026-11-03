from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from emergenzix_api.main import create_app


def _bars(*, timeframe: str, seed: int) -> list[dict[str, object]]:
    step = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}[
        timeframe
    ]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, object]] = []
    previous = 100.0 + seed
    for index in range(100):
        close = 100.0 + seed + index * (0.04 + seed * 0.001) + math.sin(index / 5) * 2
        rows.append(
            {
                "timestamp": (start + step * index).isoformat(),
                "open": previous,
                "high": max(previous, close) + 1.0,
                "low": min(previous, close) - 1.0,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
        previous = close
    return rows


@pytest.mark.asyncio
async def test_fixed_turtle_matrix_persists_ranked_oos_evidence(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EMERGENZIX_SEED_BUILTIN_STRATEGIES", "true")
    db_path = tmp_path / "matrix.db"
    app = create_app(
        db_path=db_path,
        notification_db_path=tmp_path / "notifications.db",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        datasets = []
        for symbol_index, symbol in enumerate(("BTCUSDT", "ETHUSDT", "BNBUSDT")):
            for timeframe_index, timeframe in enumerate(("1h", "4h", "1d")):
                response = await client.post(
                    "/api/v1/datasets",
                    json={
                        "source": "ccxt_public_ohlcv",
                        "exchange": "binance",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "bars": _bars(
                            timeframe=timeframe,
                            seed=symbol_index * 3 + timeframe_index,
                        ),
                    },
                )
                assert response.status_code == 201, response.text
                datasets.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "dataset_id": response.json()["id"],
                    }
                )

        response = await client.post(
            "/api/v1/turtle-matrices",
            json={
                "datasets": datasets,
                "train_fraction": 0.7,
                "initial_cash": 10_000,
                "fee_bps": 10,
                "slippage_bps": 5,
            },
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["matrix_shape"] == {
            "assets": 3,
            "timeframes": 3,
            "systems": 2,
            "arms": 18,
        }
        assert len(result["ranking"]) == 18
        assert [row["rank"] for row in result["ranking"]] == list(range(1, 19))
        assert all(row["out_of_sample"]["bars_count"] == 30 for row in result["ranking"])
        assert result["safety"]["live_order_permission"] is False
        assert result["safety"]["automatic_strategy_promotion"] is False

        listed = await client.get("/api/v1/turtle-matrices")
        detail = await client.get(f"/api/v1/turtle-matrices/{result['id']}")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [result["id"]]
        assert detail.json() == result

    with sqlite3.connect(db_path) as connection, pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ):
        connection.execute(
            "UPDATE turtle_matrix_runs SET protocol_version = 'changed' WHERE id = ?",
            (result["id"],),
        )


@pytest.mark.asyncio
async def test_turtle_matrix_rejects_incomplete_grid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMERGENZIX_SEED_BUILTIN_STRATEGIES", "true")
    app = create_app(
        db_path=tmp_path / "matrix.db",
        notification_db_path=tmp_path / "notifications.db",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/turtle-matrices",
            json={"datasets": []},
        )
    assert response.status_code == 422
