"""Public market-data access through CCXT; this module never submits orders."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import ccxt


SUPPORTED_EXCHANGES = frozenset({"binance", "bybit", "okx", "kraken", "coinbase"})
SUPPORTED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d"})
QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD", "BTC", "ETH", "EUR", "GBP", "JPY")


class MarketDataUnavailable(RuntimeError):
    """Raised when an exchange cannot provide the requested public candles."""


def normalize_symbol(symbol: str) -> str:
    """Accept CCXT's BASE/QUOTE form or common concatenated symbols such as BTCUSDT."""
    normalized = symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" in normalized:
        parts = normalized.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("symbol must use BASE/QUOTE format, for example BTC/USDT")
        base, quote = parts
        if not base.isalnum() or not quote.isalnum():
            raise ValueError("symbol may only contain letters, digits, and one slash")
        return f"{base}/{quote}"

    if not normalized.isalnum():
        raise ValueError("symbol must use BASE/QUOTE format, for example BTC/USDT")

    for quote in QUOTE_SUFFIXES:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            base = normalized[: -len(quote)]
            return f"{base}/{quote}"

    raise ValueError(
        "symbol must use BASE/QUOTE format, for example BTC/USDT, "
        "or a supported concatenated symbol such as BTCUSDT"
    )


def fetch_public_ohlcv(
    *,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    limit: int,
) -> dict[str, Any]:
    """Fetch public spot OHLCV candles without API keys or private exchange methods."""
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"Unsupported exchange '{exchange_id}'. "
            f"Choose one of: {', '.join(sorted(SUPPORTED_EXCHANGES))}"
        )
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. "
            f"Choose one of: {', '.join(sorted(SUPPORTED_TIMEFRAMES))}"
        )
    if limit < 5 or limit > 1000:
        raise ValueError("limit must be between 5 and 1000")

    normalized_symbol = normalize_symbol(symbol)
    exchange_type = getattr(ccxt, exchange_id, None)
    if exchange_type is None:
        raise ValueError(f"CCXT does not provide the '{exchange_id}' exchange")

    exchange = exchange_type(
        {
            "enableRateLimit": True,
            "timeout": 15_000,
            "options": {"defaultType": "spot"},
        }
    )

    try:
        markets = exchange.load_markets()
        if normalized_symbol not in markets:
            raise ValueError(
                f"Symbol '{normalized_symbol}' is not available on {exchange_id}"
            )

        raw_candles = exchange.fetch_ohlcv(
            normalized_symbol,
            timeframe=timeframe,
            limit=limit,
        )
        bars: list[dict[str, Any]] = []
        for candle in raw_candles:
            if not candle or len(candle) < 6:
                continue

            timestamp_ms, open_price, high, low, close, volume = candle[:6]
            values = [open_price, high, low, close, volume]
            try:
                timestamp_value = int(timestamp_ms)
                open_value, high_value, low_value, close_value, volume_value = (
                    float(value) for value in values
                )
            except (TypeError, ValueError, OverflowError):
                continue

            if not all(
                math.isfinite(value)
                for value in (open_value, high_value, low_value, close_value, volume_value)
            ):
                continue
            if min(open_value, high_value, low_value, close_value) <= 0 or volume_value < 0:
                continue
            if high_value < max(open_value, close_value, low_value):
                continue
            if low_value > min(open_value, close_value, high_value):
                continue

            bars.append(
                {
                    "timestamp": datetime.fromtimestamp(
                        timestamp_value / 1000.0, tz=timezone.utc
                    ).isoformat(),
                    "open": open_value,
                    "high": high_value,
                    "low": low_value,
                    "close": close_value,
                    "volume": volume_value,
                }
            )

        if len(bars) < 5:
            raise MarketDataUnavailable(
                f"{exchange_id} returned fewer than five valid candles for "
                f"{normalized_symbol} ({timeframe})"
            )

        return {
            "source": "ccxt_public_ohlcv",
            "exchange": exchange_id,
            "symbol": normalized_symbol,
            "timeframe": timeframe,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "bars_count": len(bars),
            "bars": bars,
            "limitations": [
                "Public exchange data only; no credentials or private endpoints are used.",
                "The latest candle may still be forming.",
                "Exchange outages, rate limits, market availability, and regional restrictions may affect results.",
            ],
        }
    except ccxt.BaseError as exc:
        raise MarketDataUnavailable(
            f"Public market-data request failed for {exchange_id}; "
            "check exchange availability and retry later"
        ) from exc
    finally:
        try:
            exchange.close()
        except ccxt.BaseError:
            pass
