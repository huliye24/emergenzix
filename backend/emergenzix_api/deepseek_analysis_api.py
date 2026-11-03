"""Read-only API route for bounded DeepSeek research analysis."""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .deepseek_analysis import DeepSeekAnalysisError, analyze_research_evidence


router = APIRouter(prefix="/api/v1/research/ai-analysis", tags=["research"])


class AIAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=1_000)
    evidence: dict[str, Any]


@router.post("")
def create_ai_analysis(
    payload: AIAnalysisCreate,
    access_token: str | None = Header(
        default=None, alias="X-Emergenzix-AI-Token", max_length=512
    ),
) -> dict[str, Any]:
    """Analyze supplied research evidence; this route cannot create orders."""
    _authorize_internal_request(access_token)
    try:
        return analyze_research_evidence(
            title=payload.title,
            question=payload.question,
            evidence=payload.evidence,
        )
    except DeepSeekAnalysisError as exc:
        if exc.reason in {"not_configured", "unsupported_model", "invalid_configuration"}:
            raise HTTPException(status_code=503, detail="AI analysis is unavailable") from exc
        if exc.reason in {
            "invalid_evidence",
            "evidence_too_large",
            "sensitive_evidence",
        }:
            raise HTTPException(status_code=422, detail="Evidence is not safe to analyze") from exc
        raise HTTPException(status_code=502, detail="AI provider response was unusable") from exc


def _authorize_internal_request(provided: str | None) -> None:
    expected = os.getenv("EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="AI analysis is unavailable")
    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="AI analysis authentication failed")
