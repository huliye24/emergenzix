"""Venue-preserving candle-v2 normalization and aggregation contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "emergenzix-market-candle-v2"
OPTIONAL_VOLUME_FIELDS = (
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
)


def _utc(value: Any, *, field: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def normalize_candle_v2(
    source: Mapping[str, Any], *, interval: timedelta = timedelta(minutes=15)
) -> dict[str, Any]:
    """Normalize one closed canonical venue candle without inventing missing fields."""
    timestamp = _utc(source["timestamp"], field="timestamp")
    close_time = _utc(source["close_time"], field="close_time")
    available_at = _utc(source["available_at"], field="available_at")
    if close_time != timestamp + interval:
        raise ValueError("close_time must equal timestamp plus interval")
    if available_at < close_time:
        raise ValueError("available_at cannot precede close_time")
    if source.get("is_closed") is not True:
        raise ValueError("canonical candle must be closed")

    prices = {key: float(source[key]) for key in ("open", "high", "low", "close")}
    if min(prices.values()) <= 0:
        raise ValueError("prices must be positive")
    if prices["high"] < max(prices["open"], prices["low"], prices["close"]):
        raise ValueError("invalid candle high")
    if prices["low"] > min(prices["open"], prices["high"], prices["close"]):
        raise ValueError("invalid candle low")

    base_volume = float(source["base_volume"])
    if base_volume < 0:
        raise ValueError("base_volume must be non-negative")
    optional: dict[str, float | int | None] = {}
    for field in OPTIONAL_VOLUME_FIELDS:
        raw = source.get(field)
        if raw is None:
            optional[field] = None
            continue
        if field == "trade_count" and float(raw) != int(raw):
            raise ValueError("trade_count must be an integer")
        value: float | int = int(raw) if field == "trade_count" else float(raw)
        if value < 0:
            raise ValueError(f"{field} must be non-negative")
        optional[field] = value
    if (
        optional["taker_buy_base_volume"] is not None
        and optional["taker_buy_base_volume"] > base_volume
    ):
        raise ValueError("taker-buy base volume exceeds total base volume")
    if (
        optional["taker_buy_quote_volume"] is not None
        and optional["quote_volume"] is not None
        and optional["taker_buy_quote_volume"] > optional["quote_volume"]
    ):
        raise ValueError("taker-buy quote volume exceeds total quote volume")

    return {
        "timestamp": timestamp,
        "close_time": close_time,
        "available_at": available_at,
        **prices,
        "base_volume": base_volume,
        **optional,
        "is_closed": True,
        "is_imputed": bool(source.get("is_imputed", False)),
    }


def aggregate_candles_v2(
    candles: Sequence[Mapping[str, Any]], *, target_interval: timedelta
) -> dict[str, Any]:
    """Aggregate a complete contiguous sequence; nullable metrics stay unknown."""
    if (
        not candles
        or target_interval < timedelta(minutes=15)
        or target_interval % timedelta(minutes=15)
    ):
        raise ValueError("target interval must be a positive multiple of 15 minutes")
    normalized = [normalize_candle_v2(item) for item in candles]
    normalized.sort(key=lambda item: item["timestamp"])
    expected = int(target_interval / timedelta(minutes=15))
    if len(normalized) != expected:
        raise ValueError("aggregation input is incomplete")
    start = normalized[0]["timestamp"]
    if start.timestamp() % int(target_interval.total_seconds()):
        raise ValueError("aggregation input is off the target boundary")
    for left, right in zip(normalized, normalized[1:]):
        if right["timestamp"] - left["timestamp"] != timedelta(minutes=15):
            raise ValueError("aggregation input is not contiguous")

    def optional_sum(field: str) -> float | int | None:
        values = [item[field] for item in normalized]
        return None if any(value is None for value in values) else sum(values)

    return {
        "timestamp": start,
        "close_time": start + target_interval,
        "available_at": max(item["available_at"] for item in normalized),
        "open": normalized[0]["open"],
        "high": max(item["high"] for item in normalized),
        "low": min(item["low"] for item in normalized),
        "close": normalized[-1]["close"],
        "base_volume": sum(item["base_volume"] for item in normalized),
        **{field: optional_sum(field) for field in OPTIONAL_VOLUME_FIELDS},
        "is_closed": True,
        "is_imputed": any(item["is_imputed"] for item in normalized),
    }
