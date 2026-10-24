"""API endpoint for importing verified historical BTC 4-hour spot data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import Field, model_validator

from .database import Database
from .dataset_store import persist_dataset
from .historical_data import (
    HistoricalArchiveUnavailable,
    fetch_binance_btc_4h_archive,
)
from .schemas import DatasetCreate, StrictModel

router = APIRouter(prefix="/api/v1/datasets/import", tags=["datasets"])
_MAX_RANGE = timedelta(days=3660)


class BinanceBTC4HImport(StrictModel):
    """Import a UTC time range of BTCUSDT spot 4-hour candles."""

    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "BinanceBTC4HImport":
        for name, value in (("start_at", self.start_at), ("end_at", self.end_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone offset")
        start = self.start_at.astimezone(timezone.utc)
        end = self.end_at.astimezone(timezone.utc)
        if end <= start:
            raise ValueError("end_at must be later than start_at")
        if end - start > _MAX_RANGE:
            raise ValueError("historical import range cannot exceed ten years")
        if end > datetime.now(timezone.utc):
            raise ValueError("end_at cannot be in the future")
        return self


@router.post("/binance-btc-4h", status_code=status.HTTP_201_CREATED)
def import_binance_btc_4h(
    payload: BinanceBTC4HImport,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Download verified official archives and register an immutable BTC dataset."""
    try:
        bars = fetch_binance_btc_4h_archive(
            start_at=payload.start_at,
            end_at=payload.end_at,
        )
        dataset_payload = DatasetCreate(
            source="binance_public_archive",
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="4h",
            bars=bars,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HistoricalArchiveUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db: Database = request.app.state.database
    try:
        row, inserted = persist_dataset(db, dataset_payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not inserted:
        response.status_code = status.HTTP_200_OK

    return {
        "id": row["id"],
        "sha256": row["content_sha256"],
        "source": row["source"],
        "exchange": row["exchange"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "bars_count": int(row["bars_count"]),
        "created_at": row["created_at"],
        "import": {
            "provider": "Binance Public Data",
            "market_type": "spot",
            "interval": "4h",
            "checksum_verified": True,
        },
    }
