"""Read-only HTTP adapter for comparison of saved date-range research records."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .database import Database
from .date_range_comparison import (
    DateRangeComparisonError,
    compare_saved_date_range_records,
)


router = APIRouter(prefix="/api/v1/research/date-range-comparisons", tags=["research"])


@router.get("")
def compare_date_range_backtests(
    request: Request,
    record_ids: list[str] = Query(
        ...,
        min_length=2,
        max_length=10,
        description="Repeat record_ids for each saved 64-character SHA-256 record ID.",
    ),
) -> dict[str, Any]:
    """Compare persisted observations; never fetch market data or execute orders."""
    if len(set(record_ids)) != len(record_ids):
        raise HTTPException(status_code=422, detail="record IDs must be unique")
    if any(
        len(record_id) != 64
        or any(ch not in "0123456789abcdef" for ch in record_id)
        for record_id in record_ids
    ):
        raise HTTPException(
            status_code=422,
            detail="record IDs must be 64-character lowercase hexadecimal strings",
        )

    database: Database = request.app.state.database
    try:
        return compare_saved_date_range_records(database.path, record_ids)
    except DateRangeComparisonError as exc:
        # Do not echo local database paths, raw records, or internal SQL errors.
        message = str(exc)
        if message.startswith("record IDs not found:"):
            raise HTTPException(
                status_code=404, detail="One or more saved backtest records were not found"
            ) from exc
        if message.startswith(("database does not exist:", "date-range records could not be read")):
            raise HTTPException(
                status_code=503, detail="Saved research records are temporarily unavailable"
            ) from exc
        raise HTTPException(
            status_code=409,
            detail="Saved backtest records failed integrity or protocol validation",
        ) from exc
