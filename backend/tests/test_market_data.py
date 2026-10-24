from __future__ import annotations

from datetime import datetime, timezone

import ccxt
import pytest

from emergenzix_api.market_data import (
    MarketDataUnavailable,
    fetch_public_ohlcv,
    normalize_symbol,
)


def test_normalize_symbol_accepts_concatenated_and_ccxt_forms():
    assert normalize_symbol("BTCUSDT") == "BTC/USDT"
    assert normalize_symbol("btc/usdt") == "BTC/USDT"
    assert normalize_symbol("ETH-USD") == "ETH/USD"


def test_normalize_symbol_rejects_unknown_format():
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        normalize_symbol("BTC")


def test_fetch_public_ohlcv_uses_public_ccxt_methods(monkeypatch):
    timestamp_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    class FakeExchange:
        def __init__(self, options):
            assert options["enableRateLimit"] is True
            assert options["options"]["defaultType"] == "spot"
            self.closed = False

        def load_markets(self):
            return {"BTC/USDT": {"symbol": "BTC/USDT"}}

        def fetch_ohlcv(self, symbol, *, timeframe, limit):
            assert symbol == "BTC/USDT"
            assert timeframe == "1h"
            assert limit == 5
            return [
                [timestamp_ms + index * 3_600_000, 100, 110, 90, 105, 12]
                for index in range(5)
            ]

        def close(self):
            self.closed = True

    monkeypatch.setattr(ccxt, "binance", FakeExchange)
    payload = fetch_public_ohlcv(
        exchange_id="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        limit=5,
    )

    assert payload["source"] == "ccxt_public_ohlcv"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["bars_count"] == 5
    assert payload["bars"][0]["timestamp"].endswith("+00:00")
    assert payload["bars"][0]["close"] == 105.0


def test_fetch_public_ohlcv_rejects_unavailable_symbol(monkeypatch):
    class FakeExchange:
        def __init__(self, options):
            pass

        def load_markets(self):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(ccxt, "binance", FakeExchange)
    with pytest.raises(ValueError, match="not available"):
        fetch_public_ohlcv(
            exchange_id="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            limit=5,
        )


def test_fetch_public_ohlcv_maps_network_errors(monkeypatch):
    class FakeExchange:
        def __init__(self, options):
            pass

        def load_markets(self):
            raise ccxt.NetworkError("private internal detail")

        def close(self):
            pass

    monkeypatch.setattr(ccxt, "binance", FakeExchange)
    with pytest.raises(MarketDataUnavailable, match="Public market-data request failed"):
        fetch_public_ohlcv(
            exchange_id="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            limit=5,
        )


def test_fetch_public_ohlcv_accepts_sync_exchange_without_close(monkeypatch):
    timestamp_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    class FakeExchange:
        def __init__(self, options):
            pass

        def load_markets(self):
            return {"BTC/USDT": {"symbol": "BTC/USDT"}}

        def fetch_ohlcv(self, symbol, *, timeframe, limit):
            return [
                [timestamp_ms + index * 3_600_000, 100, 110, 90, 105, 12]
                for index in range(5)
            ]

    monkeypatch.setattr(ccxt, "binance", FakeExchange)
    payload = fetch_public_ohlcv(
        exchange_id="binance", symbol="BTCUSDT", timeframe="1h", limit=5
    )
    assert payload["bars_count"] == 5
