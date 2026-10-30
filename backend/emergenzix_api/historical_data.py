"""Verified downloads from allowlisted Binance spot 4-hour archives."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx


BASE_URL = "https://data.binance.vision/data/spot"
MAX_RANGE_DAYS = 3660
MAX_BARS = 50_000
MAX_BARS_BY_INTERVAL = {"15m": 500_000, "1h": 150_000, "4h": MAX_BARS,
                        "1d": 20_000, "1w": 5_000, "1mo": 2_000}
SUPPORTED_SPOT_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "BNBUSDT"})
SUPPORTED_KLINE_INTERVALS = frozenset({"15m", "1h", "4h", "1d", "1w", "1mo"})


class HistoricalArchiveUnavailable(RuntimeError):
    """Raised when an official archive cannot be downloaded or verified."""


def _month_starts(start_at: datetime, end_at: datetime):
    current = datetime(start_at.year, start_at.month, 1, tzinfo=timezone.utc)
    last = datetime(end_at.year, end_at.month, 1, tzinfo=timezone.utc)
    while current <= last:
        yield current
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)


def _fetch_checked_archive(
    client: Any, url: str,
    observer: Callable[[str, bytes, str, str], None] | None = None,
) -> bytes | None:
    """Download a ZIP only after verifying its official SHA-256 checksum."""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise HistoricalArchiveUnavailable("Could not reach Binance historical data archive") from exc

    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HistoricalArchiveUnavailable(
            f"Binance archive request failed with HTTP {response.status_code}"
        ) from exc

    try:
        checksum_response = client.get(f"{url}.CHECKSUM")
        checksum_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HistoricalArchiveUnavailable(
            "Archive checksum could not be retrieved; refusing unverified historical data"
        ) from exc

    match = re.search(r"\b([0-9a-fA-F]{64})\b", checksum_response.text)
    if match is None:
        raise HistoricalArchiveUnavailable("Archive checksum file has an invalid format")
    expected = match.group(1).lower()
    actual = hashlib.sha256(response.content).hexdigest()
    if actual != expected:
        raise HistoricalArchiveUnavailable(
            f"SHA-256 mismatch for Binance archive {url.rsplit('/', 1)[-1]}"
        )
    if observer is not None:
        observer(url, response.content, checksum_response.text, actual)
    return response.content


def _timestamp_from_archive(value: str) -> datetime:
    raw = int(value)
    # Binance spot archives use microseconds for timestamps from 2025 onward;
    # older archives use milliseconds.
    divisor = 1_000_000 if raw >= 100_000_000_000_000 else 1_000
    return datetime.fromtimestamp(raw / divisor, tz=timezone.utc)


def _parse_kline_archive(
    payload: bytes, *, start_at: datetime, end_at: datetime
) -> list[dict[str, object]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise HistoricalArchiveUnavailable(
                    "Expected exactly one CSV file in the Binance kline archive"
                )
            with archive.open(csv_names[0]) as stream:
                text_stream = io.TextIOWrapper(stream, encoding="utf-8", newline="")
                reader = csv.reader(text_stream)
                bars: list[dict[str, object]] = []
                for row in reader:
                    if len(row) < 6:
                        continue
                    try:
                        timestamp = _timestamp_from_archive(row[0])
                        open_price, high, low, close, volume = (
                            float(value) for value in row[1:6]
                        )
                    except (ValueError, OverflowError, OSError):
                        # Skip a possible header or malformed line; data quality
                        # checks on the registered dataset remain a separate step.
                        continue

                    values = (open_price, high, low, close, volume)
                    if not all(math.isfinite(value) for value in values):
                        continue
                    if min(open_price, high, low, close) <= 0 or volume < 0:
                        continue
                    if high < max(open_price, close, low):
                        continue
                    if low > min(open_price, close, high):
                        continue
                    if timestamp < start_at or timestamp > end_at:
                        continue

                    bars.append(
                        {
                            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                            "open": open_price,
                            "high": high,
                            "low": low,
                            "close": close,
                            "volume": volume,
                        }
                    )
                return bars
    except (zipfile.BadZipFile, OSError) as exc:
        raise HistoricalArchiveUnavailable("Downloaded Binance ZIP is not a valid archive") from exc


def fetch_binance_spot_kline_archive(
    *, symbol: str, interval: str, start_at: datetime, end_at: datetime,
    archive_observer: Callable[[str, bytes, str, str], None] | None = None,
) -> list[dict[str, object]]:
    """Fetch allowlisted spot klines from checksum-verified Binance archives."""
    normalized_symbol = symbol.strip().upper().replace("/", "")
    if normalized_symbol not in SUPPORTED_SPOT_SYMBOLS:
        raise ValueError(
            f"unsupported Binance archive symbol; choose one of {sorted(SUPPORTED_SPOT_SYMBOLS)}"
        )
    if interval not in SUPPORTED_KLINE_INTERVALS:
        raise ValueError(
            f"unsupported Binance archive interval; choose one of {sorted(SUPPORTED_KLINE_INTERVALS)}"
        )
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise ValueError("start_at must include a timezone offset")
    if end_at.tzinfo is None or end_at.utcoffset() is None:
        raise ValueError("end_at must include a timezone offset")
    start_utc = start_at.astimezone(timezone.utc)
    end_utc = end_at.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise ValueError("end_at must be later than start_at")
    if end_utc - start_utc > timedelta(days=MAX_RANGE_DAYS):
        raise ValueError("historical import range cannot exceed ten years")
    if end_utc > datetime.now(timezone.utc):
        raise ValueError("end_at cannot be in the future")

    now_date = datetime.now(timezone.utc).date()
    collected: dict[datetime, dict[str, object]] = {}

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for month_start in _month_starts(start_utc, end_utc):
                month_key = f"{month_start.year:04d}-{month_start.month:02d}"
                monthly_url = (
                    f"{BASE_URL}/monthly/klines/{normalized_symbol}/{interval}/"
                    f"{normalized_symbol}-{interval}-{month_key}.zip"
                )
                # The current month archive is normally published after month end.
                monthly_payload = (
                    None
                    if (month_start.year, month_start.month) == (now_date.year, now_date.month)
                    else _fetch_checked_archive(client, monthly_url, archive_observer)
                )
                if monthly_payload is not None:
                    monthly_bars = _parse_kline_archive(
                        monthly_payload, start_at=start_utc, end_at=end_utc
                    )
                    for bar in monthly_bars:
                        stamp = datetime.fromisoformat(
                            str(bar["timestamp"]).replace("Z", "+00:00")
                        ).astimezone(timezone.utc)
                        collected[stamp] = bar
                    continue

                month_last_day = (
                    date(month_start.year + (month_start.month == 12),
                         1 if month_start.month == 12 else month_start.month + 1,
                         1)
                    - timedelta(days=1)
                )
                first_day = max(start_utc.date(), month_start.date())
                last_day = min(end_utc.date(), month_last_day)
                current_day = first_day
                while current_day <= last_day:
                    # Daily archive files are published after the UTC day ends.
                    if current_day == now_date:
                        current_day += timedelta(days=1)
                        continue
                    day_key = current_day.isoformat()
                    daily_url = (
                        f"{BASE_URL}/daily/klines/{normalized_symbol}/{interval}/"
                        f"{normalized_symbol}-{interval}-{day_key}.zip"
                    )
                    daily_payload = _fetch_checked_archive(client, daily_url, archive_observer)
                    if daily_payload is None:
                        raise HistoricalArchiveUnavailable(
                            f"Missing Binance daily archive for {day_key}; "
                            "refusing to silently create an incomplete dataset"
                        )
                    daily_bars = _parse_kline_archive(
                        daily_payload, start_at=start_utc, end_at=end_utc
                    )
                    for bar in daily_bars:
                        stamp = datetime.fromisoformat(
                            str(bar["timestamp"]).replace("Z", "+00:00")
                        ).astimezone(timezone.utc)
                        collected[stamp] = bar
                    current_day += timedelta(days=1)
    except httpx.HTTPError as exc:
        raise HistoricalArchiveUnavailable(
            "Binance historical archive request failed; retry later"
        ) from exc

    bars = [collected[timestamp] for timestamp in sorted(collected)]
    if len(bars) < 5:
        raise HistoricalArchiveUnavailable(
            f"The requested range produced fewer than five verified {normalized_symbol} {interval} candles"
        )
    interval_limit = MAX_BARS_BY_INTERVAL[interval]
    if len(bars) > interval_limit:
        raise ValueError(
            f"historical import exceeds the {interval_limit}-bar {interval} safety limit"
        )
    return bars


def fetch_binance_spot_4h_archive(
    *, symbol: str, start_at: datetime, end_at: datetime
) -> list[dict[str, object]]:
    return fetch_binance_spot_kline_archive(
        symbol=symbol, interval="4h", start_at=start_at, end_at=end_at
    )


def fetch_binance_btc_4h_archive(
    *, start_at: datetime, end_at: datetime
) -> list[dict[str, object]]:
    """Backward-compatible BTC wrapper used by existing research workflows."""
    return fetch_binance_spot_4h_archive(
        symbol="BTCUSDT", start_at=start_at, end_at=end_at
    )
