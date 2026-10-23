from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _bars(closes: list[float]) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = []
    for index, close in enumerate(closes):
        timestamp = start + timedelta(days=index)
        result.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": 1000.0,
            }
        )
    return result


def _create_strategy(client, *, symbol: str = "BTCUSDT") -> dict[str, object]:
    response = client.post(
        "/api/v1/strategies",
        json={
            "name": "Test SMA",
            "symbol": symbol,
            "strategy_type": "sma_crossover",
            "fast_window": 2,
            "slow_window": 3,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_is_paper_only(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"
    assert response.json()["live_trading_enabled"] is False


def test_strategy_validation_and_persistence(client):
    invalid = client.post(
        "/api/v1/strategies",
        json={
            "name": "Invalid",
            "symbol": "BTCUSDT",
            "fast_window": 5,
            "slow_window": 3,
        },
    )
    assert invalid.status_code == 422

    strategy = _create_strategy(client)
    listed = client.get("/api/v1/strategies")
    assert listed.status_code == 200
    assert any(item["id"] == strategy["id"] for item in listed.json())


def test_backtest_runs_and_is_persisted(client):
    strategy = _create_strategy(client)
    closes = [10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]
    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": strategy["id"],
            "bars": _bars(closes),
            "initial_cash": 10_000,
            "fee_bps": 10,
            "slippage_bps": 5,
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["bars_count"] == len(closes)
    assert result["final_equity"] > 0
    assert result["number_of_trades"] >= 1
    assert result["assumptions"]["execution"].startswith("signal at completed close")

    stored = client.get(f"/api/v1/backtests/{result['id']}")
    assert stored.status_code == 200
    assert stored.json()["id"] == result["id"]


def test_backtest_rejects_non_increasing_timestamps(client):
    strategy = _create_strategy(client)
    bars = _bars([10, 9, 8, 9, 11, 13])
    bars[2]["timestamp"] = bars[1]["timestamp"]
    response = client.post(
        "/api/v1/backtests",
        json={"strategy_id": strategy["id"], "bars": bars},
    )
    assert response.status_code == 422


def test_paper_buy_sell_updates_cash_positions_and_realized_pnl(client):
    buy = client.post(
        "/api/v1/paper/orders",
        json={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": 1,
            "reference_price": 1000,
        },
    )
    assert buy.status_code == 201, buy.text
    assert buy.json()["status"] == "FILLED"

    positions = client.get("/api/v1/paper/positions").json()
    assert len(positions) == 1
    assert positions[0]["quantity"] == 1.0

    sell = client.post(
        "/api/v1/paper/orders",
        json={
            "symbol": "BTCUSDT",
            "side": "SELL",
            "quantity": 1,
            "reference_price": 1100,
        },
    )
    assert sell.status_code == 201, sell.text
    assert sell.json()["realized_pnl"] > 0
    assert client.get("/api/v1/paper/positions").json() == []

    account = client.get("/api/v1/paper/account").json()
    assert account["cash"] > account["starting_cash"]
    assert account["realized_pnl"] > 0


def test_paper_order_risk_limits_and_no_shorting(client):
    too_large = client.post(
        "/api/v1/paper/orders",
        json={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": 3,
            "reference_price": 1000,
        },
    )
    assert too_large.status_code == 422

    short_sale = client.post(
        "/api/v1/paper/orders",
        json={
            "symbol": "BTCUSDT",
            "side": "SELL",
            "quantity": 1,
            "reference_price": 1000,
        },
    )
    assert short_sale.status_code == 409

    account = client.get("/api/v1/paper/account").json()
    assert account["cash"] == account["starting_cash"]


def test_order_cannot_be_linked_to_a_different_symbol_strategy(client):
    strategy = _create_strategy(client, symbol="BTCUSDT")
    response = client.post(
        "/api/v1/paper/orders",
        json={
            "symbol": "ETHUSDT",
            "side": "BUY",
            "quantity": 1,
            "reference_price": 100,
            "strategy_id": strategy["id"],
        },
    )
    assert response.status_code == 422


def test_public_market_data_endpoint_uses_provider_without_network(client, monkeypatch):
    expected_bars = _bars([10, 11, 12, 13, 14, 15])

    def fake_fetch_public_ohlcv(*, exchange_id, symbol, timeframe, limit):
        assert exchange_id == "binance"
        assert symbol == "BTCUSDT"
        assert timeframe == "1h"
        assert limit == 120
        return {
            "source": "ccxt_public_ohlcv",
            "exchange": exchange_id,
            "symbol": "BTC/USDT",
            "timeframe": timeframe,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "bars_count": len(expected_bars),
            "bars": expected_bars,
            "limitations": [],
        }

    monkeypatch.setattr(
        "emergenzix_api.main.fetch_public_ohlcv", fake_fetch_public_ohlcv
    )
    response = client.get(
        "/api/v1/market-data/ohlcv",
        params={
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "limit": 120,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "ccxt_public_ohlcv"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["bars_count"] == 6


def test_public_market_data_endpoint_rejects_provider_input_errors(client, monkeypatch):
    def fake_fetch_public_ohlcv(**kwargs):
        raise ValueError("unsupported market symbol")

    monkeypatch.setattr(
        "emergenzix.main.fetch_public_ohlcv", fake_fetch_public_ohlcv, raising=False
    )
    monkeypatch.setattr(
        "emergenzix_api.main.fetch_public_ohlcv", fake_fetch_public_ohlcv
    )
    response = client.get(
        "/api/v1/market-data/ohlcv",
        params={"exchange": "binance", "symbol": "INVALID", "timeframe": "1h"},
    )
    assert response.status_code == 422
