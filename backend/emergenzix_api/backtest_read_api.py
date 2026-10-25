"""Read-only retrieval of persisted historical PAPER backtest records.

Keep execution and record creation in the original POST /api/v1/backtests.
The database is read from request-scoped application state, not globals.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .database import Database

router = APIRouter()


@router.get("/api/v1/backtests")
def list_backtests(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT result_json FROM backtests "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [json.loads(row["result_json"]) for row in rows]

@router.get("/api/v1/backtests/{backtest_id}")
def get_backtest(backtest_id: str, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        row = connection.execute(
            "SELECT result_json FROM backtests WHERE id = ?", (backtest_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return json.loads(row["result_json"])
