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
    assert result["data_provenance"]["source"] == "user_supplied"

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
        "emergenzix_api.main.fetch_public_ohlcv", fake_fetch_public_ohlcv
    )
    response = client.get(
        "/api/v1/market-data/ohlcv",
        params={"exchange": "binance", "symbol": "INVALID", "timeframe": "1h"},
    )
    assert response.status_code == 422



def test_backtest_persists_ccxt_dataset_provenance(client):
    strategy = _create_strategy(client)
    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": strategy["id"],
            "bars": _bars([10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]),
            "data_source": "ccxt_public_ohlcv",
            "data_exchange": "binance",
            "data_symbol": "BTC/USDT",
            "data_timeframe": "1h",
        },
    )
    assert response.status_code == 201, response.text
    provenance = response.json()["data_provenance"]
    assert provenance == {
        "source": "ccxt_public_ohlcv",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
    }


def test_ccxt_backtest_requires_complete_provenance(client):
    strategy = _create_strategy(client)
    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": strategy["id"],
            "bars": _bars([10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]),
            "data_source": "ccxt_public_ohlcv",
            "data_exchange": "binance",
        },
    )
    assert response.status_code == 422



def _bars_at_offsets(
    start: datetime, offsets_seconds: list[int]
) -> list[dict[str, object]]:
    result = []
    for offset in offsets_seconds:
        result.append(
            {
                "timestamp": (start + timedelta(seconds=offset)).isoformat(),
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.0,
                "volume": 100.0,
            }
        )
    return result


def _register_quality_dataset(client, bars, *, timeframe: str | None = "1h"):
    payload = {"symbol": "BTCUSDT", "bars": bars}
    if timeframe is not None:
        payload["timeframe"] = timeframe
    response = client.post("/api/v1/datasets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_dataset_quality_reports_gap_coverage_and_freshness(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bars = _bars_at_offsets(
        now - timedelta(hours=5),
        [0, 3600, 3 * 3600, 4 * 3600, 5 * 3600],
    )
    dataset = _register_quality_dataset(client, bars)

    response = client.get(f"/api/v1/datasets/{dataset['id']}/quality")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["sha256"] == dataset["sha256"]
    assert report["interval_seconds"] == 3600
    assert report["bars_count"] == 5
    assert report["expected_bars_count"] == 6
    assert report["missing_candles_estimate"] == 1
    assert report["coverage_pct"] == 83.333333
    assert len(report["gaps"]) == 1
    assert report["gaps"][0]["missing_candles_estimate"] == 1
    assert report["freshness"]["status"] == "within_threshold"
    assert report["diagnostic_only"] is True


def test_dataset_quality_omits_coverage_for_irregular_intervals(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bars = _bars_at_offsets(
        now - timedelta(seconds=16_200),
        [0, 3600, 9000, 12_600, 16_200],
    )
    dataset = _register_quality_dataset(client, bars)

    response = client.get(f"/api/v1/datasets/{dataset['id']}/quality")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["coverage_pct"] is None
    assert report["expected_bars_count"] is None
    assert len(report["irregular_intervals"]) == 1
    assert report["freshness"]["status"] == "within_threshold"


def test_dataset_quality_without_timeframe_marks_checks_unavailable(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bars = _bars_at_offsets(
        now - timedelta(hours=5),
        [0, 3600, 3 * 3600, 4 * 3600, 5 * 3600],
    )
    dataset = _register_quality_dataset(client, bars, timeframe=None)

    response = client.get(f"/api/v1/datasets/{dataset['id']}/quality")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["interval_seconds"] is None
    assert report["missing_candles_estimate"] is None
    assert report["coverage_pct"] is None
    assert report["gaps"] == []
    assert report["freshness"]["status"] == "not_assessable"


def test_dataset_quality_returns_404_for_unknown_dataset(client):
    response = client.get(f"/api/v1/datasets/{'f' * 64}/quality")
    assert response.status_code == 404
