"""Private API for the immutable EAMI research registry."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .eami_registry import (
    EAMIRegistryError,
    get_experiment,
    get_model,
    record_experiment,
    record_stage_event,
    register_model,
)

router = APIRouter(prefix="/api/v1/eami", tags=["eami-research"])
SHA256 = r"^[0-9a-f]{64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRegistration(_Strict):
    model_name: str = Field(min_length=1, max_length=100)
    model_version: str = Field(min_length=1, max_length=64)
    model_family: str = Field(min_length=1, max_length=100)
    feature_version: str = Field(min_length=1, max_length=100)
    training_parameters: dict[str, Any]
    code_sha256: str = Field(pattern=SHA256)
    artifact_sha256: str | None = Field(default=None, pattern=SHA256)
    parent_model_id: str | None = Field(default=None, pattern=SHA256)
    created_by: str = Field(min_length=1, max_length=100)

    @field_validator(
        "model_name", "model_version", "model_family", "feature_version", "created_by"
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        if value.strip() != value or not value:
            raise ValueError("text fields must be non-empty and trimmed")
        return value


class ExperimentRecord(_Strict):
    model_id: str = Field(pattern=SHA256)
    dataset_id: str = Field(pattern=SHA256)
    data_start_at: datetime
    data_end_at: datetime
    protocol_version: str = Field(min_length=1, max_length=100)
    split_definition: dict[str, Any]
    metrics: dict[str, Any]
    outcome: Literal["PASSED", "FAILED", "INCONCLUSIVE"]
    random_seed: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_range(self) -> "ExperimentRecord":
        if self.data_start_at.tzinfo is None or self.data_end_at.tzinfo is None:
            raise ValueError("experiment timestamps must include a timezone")
        if self.data_end_at <= self.data_start_at:
            raise ValueError("data_end_at must be after data_start_at")
        return self


class StageEvent(_Strict):
    model_id: str = Field(pattern=SHA256)
    experiment_id: str | None = Field(default=None, pattern=SHA256)
    to_stage: Literal["CHALLENGER", "CHAMPION", "REJECTED", "RETIRED"]
    rationale: str = Field(min_length=1, max_length=2000)
    recorded_by: str = Field(min_length=1, max_length=100)


def _call(operation):
    try:
        return operation()
    except EAMIRegistryError as exc:
        message = str(exc)
        status_code = (
            404 if "not registered" in message or "not recorded" in message else 409
        )
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelRegistration, request: Request) -> dict[str, Any]:
    return _call(
        lambda: register_model(request.app.state.database, payload.model_dump())
    )


@router.get("/models/{model_id}")
def read_model(model_id: str, request: Request) -> dict[str, Any]:
    return _call(lambda: get_model(request.app.state.database, model_id))


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
def create_experiment(payload: ExperimentRecord, request: Request) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    return _call(lambda: record_experiment(request.app.state.database, data))


@router.get("/experiments/{experiment_id}")
def read_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return _call(lambda: get_experiment(request.app.state.database, experiment_id))


@router.post("/stage-events", status_code=status.HTTP_201_CREATED)
def create_stage_event(payload: StageEvent, request: Request) -> dict[str, Any]:
    return _call(
        lambda: record_stage_event(request.app.state.database, payload.model_dump())
    )
