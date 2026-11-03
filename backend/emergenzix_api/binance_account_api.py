"""Authenticated-facing, read-only Binance account summary route."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .binance_account import BinanceAccountUnavailable, BinanceReadOnlyAccount


router = APIRouter(prefix="/api/v1/exchange/binance", tags=["exchange-account"])


@router.get("/account-summary")
def account_summary(request: Request) -> dict[str, object]:
    reader: BinanceReadOnlyAccount = request.app.state.binance_account_reader
    try:
        return reader.summary()
    except BinanceAccountUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

