"""Verified downloads from Binance's public historical 4-hour BTC spot archives."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
from pathlib import Path
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx


BASE_URL = "https://data.binance.vision/data/spot"
MAX_RANGE_DAYS = 3660
MAX_BARS = 50_000


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


CACHE_DIR_ENV = "BINANCE_ARCHIVE_CACHE_DIR"


def cache_directory(explicit: Path | None = None) -> Path | None:
    """Resolve the optional on-disk archive cache directory."""
    if explicit is not None:
        return Path(explicit)
    raw = os.getenv(CACHE_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def _cache_files(cache_dir: Path, symbol: str, period: str, key: str):
    base = cache_dir / symbol.upper()
    return (base / f"{symbol.upper()}-4h-{period}-{key}.zip",
            base / f"{symbol.upper()}-4h-{period}-{key}.sha256")


def _load_cached(cache_dir: Path | None, symbol: str, period: str,
                 key: str) -> bytes | None:
    """Return a cached archive whose stored digest still matches its bytes."""
    if cache_dir is None:
        return None
    zip_path, digest_path = _cache_files(cache_dir, symbol, period, key)
    if not (zip_path.is_file() and digest_path.is_file()):
        return None
    try:
        payload = zip_path.read_bytes()
        stored = digest_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    if hashlib.sha256(payload).hexdigest() != stored:
        return None
    return payload


def _store_cached(cache_dir: Path | None, symbol: str, period: str,
                  key: str, payload: bytes) -> None:
    """Persist a verified archive plus the digest that was checked on download."""
    if cache_dir is None:
        return
    zip_path, digest_path = _cache_files(cache_dir, symbol, period, key)
    try:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(payload)
        digest_path.write_text(hashlib.sha256(payload).hexdigest() + "\n",
                               encoding="utf-8")
    except OSError:
        # A cache failure must never fail a run; the data is already verified.
        return


def _fetch_checked_archive(client: Any, url: str) -> bytes | None:
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


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{3,20}$")


def fetch_binance_spot_4h_archive(
    *, symbol: str, start_at: datetime, end_at: datetime,
    cache_dir: Path | None = None,
) -> list[dict[str, object]]:
    """Fetch spot 4h bars for one symbol from verified Binance ZIP archives."""
    normalized = symbol.strip().upper()
    cache = cache_directory(cache_dir)
    if not SYMBOL_PATTERN.match(normalized):
        raise ValueError("symbol must be 3-20 uppercase alphanumeric characters")
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
                    f"{BASE_URL}/monthly/klines/{normalized}/4h/"
                    f"{normalized}-4h-{month_key}.zip"
                )
                # The current month archive is normally published after month end.
                monthly_payload = (
                    None
                    if (month_start.year, month_start.month) == (now_date.year, now_date.month)
                    else (_load_cached(cache, normalized, "monthly", month_key)
                          or _fetch_checked_archive(client, monthly_url))
                )
                if monthly_payload is not None:
                    _store_cached(cache, normalized, "monthly", month_key,
                                  monthly_payload)
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
                        f"{BASE_URL}/daily/klines/{normalized}/4h/"
                        f"{normalized}-4h-{day_key}.zip"
                    )
                    daily_payload = (
                        _load_cached(cache, normalized, "daily", day_key)
                        or _fetch_checked_archive(client, daily_url)
                    )
                    if daily_payload is not None:
                        _store_cached(cache, normalized, "daily", day_key,
                                      daily_payload)
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
            "The requested range produced fewer than five verified BTC 4h candles"
        )
    if len(bars) > MAX_BARS:
        raise ValueError(f"historical import exceeds the {MAX_BARS}-bar safety limit")
    return bars


def fetch_binance_btc_4h_archive(
    *, start_at: datetime, end_at: datetime
) -> list[dict[str, object]]:
    """Backward-compatible BTCUSDT wrapper around the generic fetcher."""
    return fetch_binance_spot_4h_archive(
        symbol="BTCUSDT", start_at=start_at, end_at=end_at
    )
