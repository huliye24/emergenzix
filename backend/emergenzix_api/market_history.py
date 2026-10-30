"""Build long and fresh Binance 4h research histories without forming candles."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .historical_data import fetch_binance_spot_kline_archive
from .market_data import fetch_public_ohlcv


FOUR_HOURS = timedelta(hours=4)
SYMBOLS = {
    "BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "BNBUSDT": "BNB/USDT"
}


class MarketHistoryUnavailable(RuntimeError):
    """Raised when a complete, reconciled research history cannot be built."""


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
    public_fetcher: Callable[..., dict[str, Any]] = fetch_public_ohlcv,
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
        exchange_id="binance", symbol=SYMBOLS[normalized], timeframe=interval, limit=1000
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
    if any(current - previous != expected_delta for previous, current in zip(timestamps, timestamps[1:])):
        raise MarketHistoryUnavailable(f"non-contiguous {interval} history")
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
