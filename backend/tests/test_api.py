from __future__ import annotations

from datetime import datetime, timedelta, timezone

from emergenzix_api.market_data import MarketDataUnavailable


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
    assert strategy["current_version"] == 1
    assert strategy["current_version_id"]

    versions = client.get(f"/api/v1/strategies/{strategy['id']}/versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    assert versions.json()[0]["version"] == 1
    assert versions.json()[0]["parameters"] == {
        "strategy_type": "sma_crossover",
        "fast_window": 2,
        "slow_window": 3,
    }

    listed = client.get("/api/v1/strategies")
    assert listed.status_code == 200
    assert any(item["id"] == strategy["id"] for item in listed.json())


def test_strategy_versions_are_immutable_and_parameters_advance(client):
    strategy = _create_strategy(client)
    first = client.get(
        f"/api/v1/strategies/{strategy['id']}/versions"
    ).json()[0]

    created = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"fast_window": 4, "slow_window": 6},
    )
    assert created.status_code == 201, created.text
    second = created.json()
    assert second["version"] == 2
    assert second["id"] != first["id"]
    assert second["parameters"]["fast_window"] == 4
    assert second["parameters"]["slow_window"] == 6
    assert second["parameters_sha256"] != first["parameters_sha256"]

    versions = client.get(
        f"/api/v1/strategies/{strategy['id']}/versions"
    ).json()
    assert [item["version"] for item in versions] == [1, 2]
    assert versions[0]["parameters"]["fast_window"] == 2
    assert versions[0]["parameters"]["slow_window"] == 3

    current = client.get(f"/api/v1/strategies/{strategy['id']}").json()
    assert current["fast_window"] == 4
    assert current["slow_window"] == 6
    assert current["current_version"] == 2
    assert current["current_version_id"] == second["id"]

    duplicate = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"fast_window": 4, "slow_window": 6},
    )
    assert duplicate.status_code == 409


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
    assert result["strategy_version_id"] == strategy["current_version_id"]
    assert result["strategy"]["version"] == 1
    assert result["strategy"]["parameters"]["fast_window"] == 2
    assert result["strategy"]["parameters"]["slow_window"] == 3
    assert result["strategy"]["parameters_sha256"]

    stored = client.get(f"/api/v1/backtests/{result['id']}")
    assert stored.status_code == 200
    assert stored.json()["id"] == result["id"]


def test_strategy_comparison_uses_same_dataset_and_persists_result(client):
    strategy = _create_strategy(client)
    first_version_id = strategy["current_version_id"]
    second_response = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"fast_window": 4, "slow_window": 6},
    )
    assert second_response.status_code == 201, second_response.text
    second_version_id = second_response.json()["id"]
    closes = [10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]
    dataset = _register_quality_dataset(client, _bars(closes), timeframe="1h")

    response = client.post(
        "/api/v1/strategy-comparisons",
        json={
            "strategy_id": strategy["id"],
            "strategy_version_ids": [first_version_id, second_version_id],
            "dataset_id": dataset["id"],
            "initial_cash": 12_000,
            "fee_bps": 12,
            "slippage_bps": 7,
        },
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["data_provenance"]["dataset_id"] == dataset["id"]
    assert result["data_provenance"]["dataset_sha256"] == dataset["sha256"]
    assert result["configuration"] == {
        "initial_cash": 12_000,
        "fee_bps": 12,
        "slippage_bps": 7,
    }
    assert [item["strategy_version_id"] for item in result["results"]] == [
        first_version_id,
        second_version_id,
    ]
    assert [item["version"] for item in result["results"]] == [1, 2]
    assert [item["parameters"]["fast_window"] for item in result["results"]] == [2, 4]
    assert all(item["bars_count"] == len(closes) for item in result["results"])
    assert all(item["parameters_sha256"] for item in result["results"])

    stored = client.get(f"/api/v1/strategy-comparisons/{result['id']}")
    assert stored.status_code == 200, stored.text
    assert stored.json() == result


def test_strategy_comparison_rejects_versions_from_another_strategy(client):
    strategy = _create_strategy(client)
    other_strategy = _create_strategy(client, symbol="ETHUSDT")
    dataset = _register_quality_dataset(
        client, _bars([10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11])
    )

    response = client.post(
        "/api/v1/strategy-comparisons",
        json={
            "strategy_id": strategy["id"],
            "strategy_version_ids": [
                strategy["current_version_id"],
                other_strategy["current_version_id"],
            ],
            "dataset_id": dataset["id"],
        },
    )

    assert response.status_code == 422


def test_strategy_comparison_rejects_duplicate_version_ids(client):
    strategy = _create_strategy(client)
    response = client.post(
        "/api/v1/strategy-comparisons",
        json={
            "strategy_id": strategy["id"],
            "strategy_version_ids": [
                strategy["current_version_id"],
                strategy["current_version_id"],
            ],
            "dataset_id": "a" * 64,
        },
    )
    assert response.status_code == 422


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
    calls = []

    def fake_fetch_public_ohlcv(*, exchange_id, symbol, timeframe, limit):
        calls.append((exchange_id, symbol, timeframe, limit))
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
    params = {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "limit": 120,
    }
    response = client.get("/api/v1/market-data/ohlcv", params=params)
    repeated = client.get("/api/v1/market-data/ohlcv", params=params)

    assert response.status_code == 200, response.text
    assert repeated.status_code == 200, repeated.text
    payload = response.json()
    assert payload["source"] == "ccxt_public_ohlcv"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["bars_count"] == 6
    assert repeated.json() == payload
    assert len(calls) == 1


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



def test_public_market_data_cache_key_includes_request_limit(client, monkeypatch):
    calls = []

    def fake_fetch_public_ohlcv(*, exchange_id, symbol, timeframe, limit):
        calls.append(limit)
        return {
            "source": "ccxt_public_ohlcv",
            "exchange": exchange_id,
            "symbol": "BTC/USDT",
            "timeframe": timeframe,
            "fetched_at": f"limit-{limit}",
            "bars_count": 5,
            "bars": _bars([10, 11, 12, 13, 14]),
            "limitations": [],
        }

    monkeypatch.setattr(
        "emergenzix_api.main.fetch_public_ohlcv", fake_fetch_public_ohlcv
    )
    first = client.get(
        "/api/v1/market-data/ohlcv",
        params={"exchange": "binance", "symbol": "BTCUSDT", "timeframe": "1h", "limit": 120},
    )
    second = client.get(
        "/api/v1/market-data/ohlcv",
        params={"exchange": "binance", "symbol": "BTCUSDT", "timeframe": "1h", "limit": 121},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["fetched_at"] == "limit-120"
    assert second.json()["fetched_at"] == "limit-121"
    assert calls == [120, 121]


def test_public_market_data_failures_are_not_cached(client, monkeypatch):
    calls = 0

    def fake_fetch_public_ohlcv(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MarketDataUnavailable("temporary exchange failure")
        return {
            "source": "ccxt_public_ohlcv",
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "fetched_at": "recovered",
            "bars_count": 5,
            "bars": _bars([10, 11, 12, 13, 14]),
            "limitations": [],
        }

    monkeypatch.setattr(
        "emergenzix_api.main.fetch_public_ohlcv", fake_fetch_public_ohlcv
    )
    params = {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "limit": 120,
    }
    failed = client.get("/api/v1/market-data/ohlcv", params=params)
    recovered = client.get("/api/v1/market-data/ohlcv", params=params)

    assert failed.status_code == 502
    assert recovered.status_code == 200
    assert recovered.json()["fetched_at"] == "recovered"
    assert calls == 2


def test_ttl_cache_expires_values_and_returns_copies():
    from emergenzix_api.market_data_cache import TTLCache

    clock_value = [0.0]
    calls = []
    cache = TTLCache(
        ttl_seconds=10,
        max_entries=2,
        clock=lambda: clock_value[0],
    )

    def factory():
        calls.append(len(calls) + 1)
        return {"value": calls[-1]}

    first = cache.get_or_create(("binance", "BTC/USDT", "1h", 120), factory)
    first["value"] = 999
    repeated = cache.get_or_create(("binance", "BTC/USDT", "1h", 120), factory)
    assert repeated == {"value": 1}
    assert len(calls) == 1

    clock_value[0] = 11.0
    expired = cache.get_or_create(("binance", "BTC/USDT", "1h", 120), factory)
    assert expired == {"value": 2}
    assert len(calls) == 2


def test_ttl_cache_evicts_least_recently_used_entry():
    from emergenzix_api.market_data_cache import TTLCache

    cache = TTLCache(ttl_seconds=30, max_entries=2)
    calls = []

    def factory(name):
        def create():
            calls.append(name)
            return {"name": name}
        return create

    cache.get_or_create("a", factory("a"))
    cache.get_or_create("b", factory("b"))
    cache.get_or_create("a", factory("a"))
    cache.get_or_create("c", factory("c"))
    cache.get_or_create("b", factory("b"))

    assert calls == ["a", "b", "c", "b"]



def test_backtest_can_reproduce_an_older_strategy_version(client):
    strategy = _create_strategy(client)
    first_version_id = strategy["current_version_id"]
    created = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"fast_window": 4, "slow_window": 6},
    )
    assert created.status_code == 201, created.text

    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": strategy["id"],
            "strategy_version_id": first_version_id,
            "bars": _bars([10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]),
        },
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["strategy_version_id"] == first_version_id
    assert result["strategy"]["version"] == 1
    assert result["strategy"]["fast_window"] == 2
    assert result["strategy"]["slow_window"] == 3


def test_backtest_rejects_a_version_from_another_strategy(client):
    first = _create_strategy(client)
    second = _create_strategy(client, symbol="ETHUSDT")
    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": first["id"],
            "strategy_version_id": second["current_version_id"],
            "bars": _bars([10, 9, 8, 9, 11, 13, 12, 10, 8, 7, 9, 12, 14, 13, 11]),
        },
    )
    assert response.status_code == 422



def test_database_backfills_legacy_strategy_version_without_guessing_old_backtests(tmp_path):
    import sqlite3

    from emergenzix_api.database import Database

    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE strategies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            fast_window INTEGER NOT NULL,
            slow_window INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE backtests (
            id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL REFERENCES strategies(id),
            symbol TEXT NOT NULL,
            bars_count INTEGER NOT NULL,
            initial_cash REAL NOT NULL,
            final_equity REAL NOT NULL,
            total_return_pct REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            number_of_trades INTEGER NOT NULL,
            win_rate_pct REAL NOT NULL,
            benchmark_return_pct REAL NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        INSERT INTO strategies
            (id, name, symbol, strategy_type, fast_window, slow_window,
             status, created_at, updated_at)
        VALUES
            ('legacy-strategy', 'Legacy SMA', 'BTCUSDT', 'sma_crossover',
             2, 3, 'draft', '2026-01-01T00:00:00+00:00',
             '2026-01-01T00:00:00+00:00');

        INSERT INTO backtests
            (id, strategy_id, symbol, bars_count, initial_cash, final_equity,
             total_return_pct, max_drawdown_pct, number_of_trades, win_rate_pct,
             benchmark_return_pct, result_json, created_at)
        VALUES
            ('legacy-backtest', 'legacy-strategy', 'BTCUSDT', 5, 10000, 10100,
             1, 0, 1, 100, 0, '{}', '2026-01-02T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    Database(str(db_path))

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    version = connection.execute(
        "SELECT * FROM strategy_versions WHERE strategy_id = ?",
        ("legacy-strategy",),
    ).fetchone()
    old_backtest = connection.execute(
        "SELECT * FROM backtests WHERE id = ?", ("legacy-backtest",)
    ).fetchone()
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(backtests)")
    }
    connection.close()

    assert version["version"] == 1
    assert version["fast_window"] == 2
    assert version["slow_window"] == 3
    assert version["parameters_sha256"]
    assert "strategy_version_id" in columns
    assert old_backtest["strategy_version_id"] is None
