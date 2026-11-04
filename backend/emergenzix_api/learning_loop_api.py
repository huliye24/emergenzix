"""API for the first-strategy PAPER learning-loop status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .learning_loop import LearningLoopStatusError, get_learning_loop_status

router = APIRouter(tags=["learning-loop"])


@router.get("/api/v1/learning-loop/status")
def learning_loop_status(request: Request) -> dict[str, Any]:
    try:
        return get_learning_loop_status(request.app.state.database)
    except LearningLoopStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
