"""PAPER-only API for the isolated BTC short strategy comparison fleet."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from .database import Database
from .schemas import StrictModel
from .short_shadow_runner import (
    ShortShadowRunnerError,
    create_btc_short_fleet,
    get_short_shadow_session,
    list_short_shadow_sessions,
)

router = APIRouter(prefix="/api/v1/short-shadow", tags=["short-shadow"])


class BtcShortFleetCreate(StrictModel):
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    timeframe: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_cash: float = Field(default=10_000.0, gt=0, le=1_000_000_000)
    fee_bps: float = Field(default=10.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=5.0, ge=0, le=1_000)


@router.post("/fleets", status_code=status.HTTP_201_CREATED)
def create_fleet(payload: BtcShortFleetCreate, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.database
    values = payload.model_dump(exclude={"symbol"})
    try:
        return create_btc_short_fleet(db, **values)
    except ShortShadowRunnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sessions")
def list_sessions(request: Request) -> list[dict[str, Any]]:
    return list_short_shadow_sessions(request.app.state.database)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    result = get_short_shadow_session(request.app.state.database, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Short-shadow session not found")
    return result
