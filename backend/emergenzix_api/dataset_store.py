"""Canonical, content-addressed storage for validated OHLCV datasets."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .database import Database
from .schemas import DatasetCreate


def persist_dataset(
    db: Database,
    payload: DatasetCreate,
    *,
    symbol: str | None = None,
) -> tuple[Any, bool]:
    """Persist a validated dataset once and return its row plus insertion status."""
    dataset_symbol = symbol if symbol is not None else payload.symbol
    canonical_bars = [
        {
            "timestamp": bar.timestamp.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        for bar in payload.bars
    ]
    canonical_payload = {
        "source": payload.source,
        "exchange": payload.exchange,
        "symbol": dataset_symbol,
        "timeframe": payload.timeframe,
        "bars": canonical_bars,
    }
    canonical_json = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    dataset_id = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    bars_json = json.dumps(
        canonical_bars,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    created_at = datetime.now(timezone.utc).isoformat()

    with db.connection() as connection:
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO market_datasets
                (id, content_sha256, source, exchange, symbol, timeframe,
                 start_at, end_at, bars_count, bars_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                dataset_id,
                payload.source,
                payload.exchange,
                dataset_symbol,
                payload.timeframe,
                canonical_bars[0]["timestamp"],
                canonical_bars[-1]["timestamp"],
                len(canonical_bars),
                bars_json,
                created_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()

    if row is None:
        raise RuntimeError("Dataset insert completed without a retrievable row")
    return row, inserted.rowcount > 0


class DatasetIntegrityError(ValueError):
    """Stored content-addressed dataset metadata or bars were modified."""


def verify_stored_dataset(row: Any) -> list[dict[str, Any]]:
    """Verify the original content hash and bar-range metadata before research.

    Accept harmless JSON whitespace/key ordering differences, but reject
    changes to candle values, symbol, source, timeframe, counts or time bounds.
    Never trust an advertised sha256 without recomputing it from the row.
    """
    try:
        bars = json.loads(row["bars_json"])
        if not isinstance(bars, list) or not bars or not all(
            isinstance(item, dict) for item in bars
        ):
            raise DatasetIntegrityError("invalid stored bars")

        canonical_payload = {
            "source": row["source"],
            "exchange": row["exchange"],
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "bars": bars,
        }
        canonical_json = json.dumps(
            canonical_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        if (
            digest != row["id"]
            or digest != row["content_sha256"]
            or row["bars_count"] != len(bars)
            or row["start_at"] != bars[0].get("timestamp")
            or row["end_at"] != bars[-1].get("timestamp")
        ):
            raise DatasetIntegrityError("stored dataset fingerprint mismatch")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DatasetIntegrityError("stored dataset failed integrity check") from exc
    return bars
