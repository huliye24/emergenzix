"""Private endpoint for deterministic dry-run weekly research batch manifests."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException

from .research_batch_plan import WeeklyResearchBatchPlanRequest, plan_weekly_research_batch

router = APIRouter(prefix="/api/v1/research/date-range-batches", tags=["research"])


@router.post("/plan")
def plan_weekly_batch(payload: WeeklyResearchBatchPlanRequest) -> dict[str, Any]:
    """Calculate planned historical weeks only; no database or exchange operations."""
    try:
        return plan_weekly_research_batch(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
