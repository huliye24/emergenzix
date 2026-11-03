"""PAPER-only shadow session configuration and audit endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from .database import Database
from .schemas import StrictModel
from .shadow_runner import (
    ShadowRunnerError,
    create_shadow_session,
    get_shadow_session,
    list_shadow_sessions,
)

router = APIRouter(tags=["shadow"])


class ShadowSessionCreate(StrictModel):
    source_backtest_id: str = Field(min_length=1, max_length=64)
    strategy_version_id: str = Field(min_length=1, max_length=64)
    symbol: Literal["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    timeframe: Literal["15m", "1h", "4h", "1d"]
    initial_cash: float = Field(gt=0, le=1_000_000_000)
    fee_bps: float = Field(default=10.0, ge=0, le=1_000)
    slippage_bps: float = Field(default=5.0, ge=0, le=1_000)


@router.post("/api/v1/shadow/sessions", status_code=status.HTTP_201_CREATED)
def create_session(payload: ShadowSessionCreate, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.database
    try:
        result = create_shadow_session(db, **payload.model_dump())
    except ShadowRunnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["execution_mode"] = "PAPER_ONLY"
    result["live_order_permission"] = False
    return result


@router.get("/api/v1/shadow/sessions")
def list_sessions(request: Request) -> list[dict[str, Any]]:
    return list_shadow_sessions(request.app.state.database)


@router.get("/api/v1/shadow/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    result = get_shadow_session(request.app.state.database, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Shadow session not found")
    return result
