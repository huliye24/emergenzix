"""Build long and fresh Binance 4h research histories without forming candles."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from .historical_data import fetch_binance_spot_kline_archive


FOUR_HOURS = timedelta(hours=4)
SYMBOLS = {
    "BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "BNBUSDT": "BNB/USDT"
}


class MarketHistoryUnavailable(RuntimeError):
    """Raised when a complete, reconciled research history cannot be built."""


def fetch_binance_public_tail(*, symbol: str, interval: str, limit: int = 1000) -> dict[str, Any]:
    """Fetch public klines from Binance's market-data-only endpoint."""
    normalized = symbol.strip().upper().replace("/", "")
    if normalized not in SYMBOLS:
        raise ValueError(f"unsupported history symbol; choose one of {sorted(SYMBOLS)}")
    if interval not in {"15m", "4h"}:
        raise ValueError("public tail currently supports 15m or 4h")
    try:
        response = httpx.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": normalized, "interval": interval, "limit": limit},
            timeout=20.0,
        )
        response.raise_for_status()
        rows = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MarketHistoryUnavailable("Binance public market-data tail is unavailable") from exc
    bars = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise MarketHistoryUnavailable("Binance public tail returned an invalid kline")
        bars.append({
            "timestamp": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
            "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
            "close": float(row[4]), "volume": float(row[5]),
            "base_volume": float(row[5]),
            "quote_volume": float(row[7]) if len(row) > 7 else None,
            "trade_count": int(row[8]) if len(row) > 8 else None,
            "taker_buy_base_volume": float(row[9]) if len(row) > 9 else None,
            "taker_buy_quote_volume": float(row[10]) if len(row) > 10 else None,
        })
    if len(bars) < 5:
        raise MarketHistoryUnavailable("Binance public tail returned fewer than five candles")
    return {"bars": bars, "bars_count": len(bars), "endpoint": "data-api.binance.vision"}


def latest_completed_4h_open(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    boundary = current.replace(hour=current.hour // 4 * 4, minute=0, second=0, microsecond=0)
    return boundary - FOUR_HOURS


def _stamp(bar: dict[str, Any]) -> datetime:
    value = bar["timestamp"]
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketHistoryUnavailable("market history contains a naive timestamp")
    return parsed.astimezone(timezone.utc)


def _same_bar(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        math.isclose(float(left[key]), float(right[key]), rel_tol=1e-10, abs_tol=1e-8)
        for key in ("open", "high", "low", "close", "volume")
    )


def _validate_contiguous(bars: list[dict[str, Any]]) -> None:
    if len(bars) < 5:
        raise MarketHistoryUnavailable("market history contains fewer than five candles")
    timestamps = [_stamp(bar) for bar in bars]
    for previous, current in zip(timestamps, timestamps[1:]):
        if current - previous != FOUR_HOURS:
            raise MarketHistoryUnavailable(
                f"non-contiguous 4h history between {previous.isoformat()} and {current.isoformat()}"
            )


def build_binance_verified_history(
    *, symbol: str, start_at: datetime, now: datetime | None = None,
    interval: str = "4h",
    archive_fetcher: Callable[..., list[dict[str, Any]]] = fetch_binance_spot_kline_archive,
    public_fetcher: Callable[..., dict[str, Any]] = fetch_binance_public_tail,
    archive_observer: Callable[[str, bytes, str, str], None] | None = None,
) -> dict[str, Any]:
    """Merge checksum archives with a reconciled public tail through the last closed bar."""
    normalized = symbol.strip().upper().replace("/", "")
    if normalized not in SYMBOLS:
        raise ValueError(f"unsupported history symbol; choose one of {sorted(SYMBOLS)}")
    start = start_at.astimezone(timezone.utc)
    seconds = {"15m": 900, "4h": 14_400}.get(interval)
    if seconds is None:
        raise ValueError("verified history currently supports 15m or 4h")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    epoch = int(current.timestamp())
    completed = datetime.fromtimestamp(epoch // seconds * seconds - seconds, tz=timezone.utc)
    if int(start.timestamp()) % seconds:
        raise ValueError(f"start_at must align to a UTC {interval} candle open")
    if completed - start < timedelta(days=730):
        raise ValueError("history must cover at least 730 days for the M0 walk-forward protocol")

    # Stop two UTC days back so delayed daily archives do not make routine sync flaky.
    archive_end = completed.replace(hour=0) - timedelta(days=2) + timedelta(hours=20)
    archive = archive_fetcher(
        symbol=normalized, interval=interval, start_at=start, end_at=archive_end,
        archive_observer=archive_observer,
    )
    public = public_fetcher(
        symbol=normalized, interval=interval, limit=1000
    )["bars"]
    archive_by_time = {_stamp(bar): bar for bar in archive}
    public_by_time = {
        _stamp(bar): bar for bar in public
        if start <= _stamp(bar) <= completed
    }
    overlap = sorted(set(archive_by_time) & set(public_by_time))
    if len(overlap) < 6:
        raise MarketHistoryUnavailable("archive and public tail have fewer than six overlap candles")
    mismatched = [stamp for stamp in overlap if not _same_bar(archive_by_time[stamp], public_by_time[stamp])]
    if mismatched:
        raise MarketHistoryUnavailable(
            f"archive/public OHLCV mismatch at {mismatched[0].isoformat()}"
        )

    merged = {**archive_by_time, **public_by_time}
    bars = [merged[stamp] for stamp in sorted(merged) if start <= stamp <= completed]
    expected_delta = timedelta(seconds=seconds)
    timestamps = [_stamp(bar) for bar in bars]
    for previous, current_stamp in zip(timestamps, timestamps[1:]):
        observed = current_stamp - previous
        if observed != expected_delta:
            missing = max(0, int(observed.total_seconds() // seconds) - 1)
            raise MarketHistoryUnavailable(
                f"non-contiguous {interval} history after {previous.isoformat()} "
                f"before {current_stamp.isoformat()} (missing={missing})"
            )
    if _stamp(bars[0]) != start or _stamp(bars[-1]) != completed:
        raise MarketHistoryUnavailable("history does not cover the requested first and latest completed bars")
    return {
        "symbol": SYMBOLS[normalized],
        "timeframe": interval,
        "bars": bars,
        "archive_bars": len(archive_by_time),
        "public_tail_bars": len(public_by_time),
        "overlap_bars": len(overlap),
        "latest_completed_at": completed.isoformat(),
        "forming_candle_excluded": True,
        "archive_checksum_verified": True,
        "public_tail_reconciled": True,
    }
