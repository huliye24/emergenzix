"""API routes for reproducible date-range BTC backtest experiments."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import Field, model_validator

from .date_range_backtest import create_date_range_backtest
from .database import Database
from .historical_data import HistoricalArchiveUnavailable
from .schemas import StrictModel


router = APIRouter(
    prefix="/api/v1/research/date-range-backtests",
    tags=["research"],
)


class DateRangeBacktestCreate(StrictModel):
    """Request a BTC historical experiment with a pre-test strategy-selection window."""

    start_date: date
    duration_days: int = Field(default=7, ge=1, le=30)
    training_days: int = Field(default=90, ge=60, le=365)
    initial_cash: float = Field(default=10_000.0, gt=0, le=1_000_000_000, allow_inf_nan=False)
    fee_bps: float = Field(default=10.0, ge=0, le=1_000, allow_inf_nan=False)
    slippage_bps: float = Field(default=5.0, ge=0, le=1_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_historical_period(self) -> "DateRangeBacktestCreate":
        end_at = datetime.combine(
            self.start_date, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(days=self.duration_days)
        if end_at > datetime.now(timezone.utc):
            raise ValueError("the requested test period must end in the past")
        return self


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _record_to_dict(row: Any) -> dict[str, Any]:
    record = json.loads(row["record_json"])
    integrity_payload = {
        key: value for key, value in record.items() if key != "created_at"
    }
    actual_hash = hashlib.sha256(
        _canonical_json(integrity_payload).encode("utf-8")
    ).hexdigest()
    if actual_hash != row["record_sha256"]:
        raise HTTPException(
            status_code=500,
            detail="Stored date-range backtest record failed its integrity check",
        )
    if record.get("protocol_version") != row["protocol_version"]:
        raise HTTPException(
            status_code=409,
            detail="Stored date-range backtest record protocol metadata is inconsistent",
        )
    return {**record, "record_sha256": row["record_sha256"]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_date_range_backtest_route(
    payload: DateRangeBacktestCreate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Fetch verified public history, select on pre-test bars, and persist a report."""
    db: Database = request.app.state.database
    try:
        record, inserted = create_date_range_backtest(
            db,
            start_date=payload.start_date,
            duration_days=payload.duration_days,
            training_days=payload.training_days,
            initial_cash=payload.initial_cash,
            fee_bps=payload.fee_bps,
            slippage_bps=payload.slippage_bps,
        )
    except HistoricalArchiveUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=500, detail="Date-range backtest could not be persisted"
        ) from exc
    if not inserted:
        response.status_code = status.HTTP_200_OK
    return record


@router.get("")
def list_date_range_backtests(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """List immutable experiment reports without downloading historical candles."""
    db: Database = request.app.state.database
    with db.connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM date_range_backtest_records
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_record_to_dict(row) for row in rows]


@router.get("/{record_id}")
def get_date_range_backtest(record_id: str, request: Request) -> dict[str, Any]:
    """Retrieve a saved experiment report and verify its integrity fingerprint."""
    if len(record_id) != 64 or any(character not in "0123456789abcdef" for character in record_id):
        raise HTTPException(status_code=404, detail="Date-range backtest record not found")
    db: Database = request.app.state.database
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM date_range_backtest_records WHERE id = ?",
            (record_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Date-range backtest record not found")
    return _record_to_dict(row)
