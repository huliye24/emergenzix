"""Immutable OHLCV dataset registration, retrieval and read-only quality routes.

Pure routing extraction: preserve original request validation, SQLite storage,
content-addressed records, responses and research provenance.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from .database import Database
from .dataset_quality import build_dataset_quality_report
from .dataset_store import persist_dataset
from .market_data import normalize_symbol
from .schemas import DatasetCreate

router = APIRouter()


def _dataset_to_dict(row: Any, *, include_bars: bool = False) -> dict[str, Any]:
    """Serialize dataset metadata, optionally including the immutable OHLCV payload."""
    result: dict[str, Any] = {
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
    }
    if include_bars:
        result["bars"] = json.loads(row["bars_json"])
    return result

@router.post("/api/v1/datasets", status_code=status.HTTP_201_CREATED)
def register_dataset(
    payload: DatasetCreate, request: Request, response: Response
) -> dict[str, Any]:
    """Persist validated OHLCV bars under a deterministic SHA-256 dataset ID."""
    db: Database = request.app.state.database
    dataset_symbol = payload.symbol
    if payload.source == "ccxt_public_ohlcv":
        try:
            dataset_symbol = normalize_symbol(payload.symbol)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    row, inserted = persist_dataset(
        db, payload, symbol=dataset_symbol
    )
    if not inserted:
        response.status_code = status.HTTP_200_OK
    return _dataset_to_dict(row)

@router.get("/api/v1/datasets")
def list_datasets(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """List dataset metadata without returning potentially large candle arrays."""
    db: Database = request.app.state.database
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM market_datasets ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_dataset_to_dict(row) for row in rows]

@router.get("/api/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
    """Retrieve the exact OHLCV bars associated with an immutable dataset ID."""
    db: Database = request.app.state.database
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _dataset_to_dict(row, include_bars=True)

@router.get("/api/v1/datasets/{dataset_id}/quality")
def get_dataset_quality(dataset_id: str, request: Request) -> dict[str, Any]:
    """Return read-only interval-gap, coverage, and freshness diagnostics."""
    db: Database = request.app.state.database
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return build_dataset_quality_report(
        dataset_id=row["id"],
        sha256=row["content_sha256"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        bars_json=row["bars_json"],
    )
