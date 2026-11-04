"""API for resumable EAMI research inference sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .eami_inference_ledger import (
    EAMIInferenceError,
    create_inference_session,
    get_inference_status,
    record_observation,
    record_outcome,
)

router = APIRouter(prefix="/api/v1/eami/inference", tags=["eami-research"])
SHA = r"^[0-9a-f]{64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionCreate(_Strict):
    model_id: str = Field(pattern=SHA)
    input_asset_id: str = Field(pattern=SHA)
    symbol: str = Field(min_length=3, max_length=30)
    timeframe: Literal["15m", "1h", "4h", "1d"]
    horizon_bars: int = Field(default=1, ge=1, le=24)
    first_expected_at: datetime


class ObservationCreate(_Strict):
    session_id: str = Field(pattern=SHA)
    completed_at: datetime
    status: Literal["PREDICTION", "FAILURE"]
    input_sha256: str | None = Field(default=None, pattern=SHA)
    direction: Literal["UP", "DOWN", "NEUTRAL"] | None = None
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    prediction: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "ObservationCreate":
        if self.status == "PREDICTION":
            if (
                self.input_sha256 is None
                or self.direction is None
                or self.score is None
            ):
                raise ValueError(
                    "predictions require input_sha256, direction, and score"
                )
            if self.error_code is not None:
                raise ValueError("predictions cannot include error_code")
        else:
            if self.error_code is None:
                raise ValueError("failures require error_code")
            if (
                self.input_sha256 is not None
                or self.direction is not None
                or self.score is not None
            ):
                raise ValueError("failures cannot include prediction fields")
        return self


class OutcomeCreate(_Strict):
    label: Literal["UP", "DOWN", "NEUTRAL"]
    actual_return: float = Field(allow_inf_nan=False)
    outcome: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime


def _call(operation):
    try:
        return operation()
    except EAMIInferenceError as exc:
        code = 404 if "not registered" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, request: Request) -> dict[str, Any]:
    return _call(
        lambda: create_inference_session(
            request.app.state.database, payload.model_dump(mode="json")
        )
    )


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str, request: Request, through_at: datetime = Query()
) -> dict[str, Any]:
    return _call(
        lambda: get_inference_status(
            request.app.state.database, session_id, through_at=through_at.isoformat()
        )
    )


@router.post("/observations", status_code=status.HTTP_201_CREATED)
def create_observation(payload: ObservationCreate, request: Request) -> dict[str, Any]:
    return _call(
        lambda: record_observation(
            request.app.state.database, payload.model_dump(mode="json")
        )
    )


@router.post(
    "/observations/{observation_id}/outcome", status_code=status.HTTP_201_CREATED
)
def create_outcome(
    observation_id: str, payload: OutcomeCreate, request: Request
) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    return _call(
        lambda: record_outcome(
            request.app.state.database,
            observation_id,
            label=data["label"],
            actual_return=data["actual_return"],
            outcome=data["outcome"],
            observed_at=data["observed_at"],
        )
    )
