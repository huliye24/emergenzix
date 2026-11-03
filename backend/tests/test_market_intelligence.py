from __future__ import annotations

from datetime import datetime, timedelta, timezone

from emergenzix_api.market_intelligence import build_market_intelligence
from emergenzix_api.main import create_app


def _payload(*, trend: float = 0.25) -> dict[str, object]:
    start = datetime(2026, 10, 30, tzinfo=timezone.utc)
    bars = []
    for index in range(60):
        close = 100 + index * trend
        bars.append(
            {
                "timestamp": (start + timedelta(hours=index)).isoformat(),
                "open": close - trend,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 1_000 + index,
            }
        )
    return {
        "source": "test_public_ohlcv",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "bars": bars,
    }


def test_snapshot_excludes_forming_bar_and_never_grants_execution() -> None:
    payload = _payload()
    latest_timestamp = datetime.fromisoformat(payload["bars"][-1]["timestamp"])
    result = build_market_intelligence(
        payload, now=latest_timestamp + timedelta(minutes=10)
    )

    assert result["freshness"]["forming_bar_excluded"] is True
    assert result["freshness"]["latest_completed_at"] == payload["bars"][-2]["timestamp"]
    assert result["prediction"]["calibrated_probability"] is False
    assert result["prediction"]["verified_model_count"] == 0
    assert result["prediction"]["model_consensus"] == "UNAVAILABLE"
    assert result["observers"]["nemt"]["order_permission"] is False
    assert len(result["candles"]) == 60
    assert result["candles"][-2]["complete"] is True
    assert result["candles"][-1]["complete"] is False
    assert result["candles"][-1]["volume"] == 1_059
    assert set(result["indicators"]) == {
        "rsi_14_simple",
        "sma_20",
        "sma_50",
        "sma_20_50_spread_pct",
        "bollinger_z_20",
        "realized_volatility_20_pct",
        "volume_ratio_20",
    }
    assert result["indicators"]["rsi_14_simple"] == 100.0
    assert [item["status"] for item in result["models"][:2]] == [
        "artifact_unavailable", "artifact_unavailable"
    ]
    assert result["safety"] == {
        "advisory_only": True,
        "execution_decision": "NO_ACTION",
        "paper_order_permission": False,
        "live_order_permission": False,
        "live_trading_enabled": False,
    }
    assert result["signal"]["order_permission"] is False


def test_stale_data_suppresses_research_signal() -> None:
    payload = _payload()
    latest_timestamp = datetime.fromisoformat(payload["bars"][-1]["timestamp"])
    result = build_market_intelligence(payload, now=latest_timestamp + timedelta(days=1))

    assert result["freshness"]["stale"] is True
    assert result["signal"]["research_signal"] == "NO_ACTION"
    assert result["signal"]["strategy_candidate"] is None


def test_market_intelligence_endpoint_is_read_only(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "emergenzix_api.main.fetch_public_ohlcv", lambda **_: _payload()
    )
    response = client.get(
        "/api/v1/market-intelligence",
        params={"symbol": "BTCUSDT", "timeframe": "1h"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["safety"]["execution_decision"] == "NO_ACTION"
    assert body["safety"]["live_order_permission"] is False


def test_market_intelligence_schema_supports_four_trading_symbols(tmp_path) -> None:
    app = create_app(
        db_path=tmp_path / "market-symbols.db",
        notification_db_path=tmp_path / "notifications.db",
    )
    operation = app.openapi()["paths"]["/api/v1/market-intelligence"]["get"]
    symbol_parameter = next(
        item for item in operation["parameters"] if item["name"] == "symbol"
    )

    assert symbol_parameter["schema"]["enum"] == [
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
        "SOLUSDT",
    ]
