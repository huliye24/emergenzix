"""Private, read-only API endpoint for saved date-range statistics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .database import Database
from .date_range_statistics import DateRangeStatisticsError, summarize_saved_date_range_records


router = APIRouter(prefix="/api/v1/research/date-range-statistics", tags=["research"])


@router.get("")
def get_saved_date_range_statistics(
    request: Request, limit: int = Query(default=30, ge=2, le=100)
) -> dict[str, Any]:
    """Summarize saved studies only; never rerun backtests or create orders."""
    database: Database = request.app.state.database
    try:
        return summarize_saved_date_range_records(database.path, limit=limit)
    except DateRangeStatisticsError as exc:
        if exc.reason == "database_unavailable":
            raise HTTPException(
                status_code=503, detail="Saved research database is unavailable"
            ) from exc
        if exc.reason == "invalid_limit":
            raise HTTPException(status_code=422, detail="Invalid record limit") from exc
        raise HTTPException(
            status_code=409, detail="Saved research records failed validation"
        ) from exc
