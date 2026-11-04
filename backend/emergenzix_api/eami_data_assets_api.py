"""Private API for the EAMI data inheritance catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .eami_data_assets import (
    EAMIDataAssetError,
    get_data_asset,
    get_data_lineage,
    record_asset_quality_event,
    register_data_asset,
)

router = APIRouter(prefix="/api/v1/eami/data-assets", tags=["eami-research"])
SHA256 = r"^[0-9a-f]{64}$"


class DataAssetRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: Literal["RAW", "CANONICAL", "FEATURE", "RESEARCH_MEMORY"]
    asset_kind: str = Field(min_length=1, max_length=100)
    storage_uri: str = Field(min_length=1, max_length=2048)
    storage_format: str = Field(min_length=1, max_length=50)
    schema_version: str = Field(min_length=1, max_length=100)
    feature_version: str | None = Field(default=None, min_length=1, max_length=100)
    content_sha256: str = Field(pattern=SHA256)
    producer_code_sha256: str | None = Field(default=None, pattern=SHA256)
    source_asset_ids: list[str] = Field(default_factory=list, max_length=100)
    symbol: str | None = Field(default=None, min_length=1, max_length=30)
    timeframe: str | None = Field(default=None, min_length=1, max_length=20)
    data_start_at: datetime | None = None
    data_end_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(min_length=1, max_length=100)

    @field_validator("source_asset_ids")
    @classmethod
    def validate_source_ids(cls, values: list[str]) -> list[str]:
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in values
        ):
            raise ValueError("source_asset_ids must contain lowercase SHA-256 IDs")
        return values

    @model_validator(mode="after")
    def validate_range(self) -> "DataAssetRegistration":
        if (self.data_start_at is None) != (self.data_end_at is None):
            raise ValueError("data_start_at and data_end_at must be supplied together")
        if self.data_start_at is not None:
            if self.data_start_at.tzinfo is None or self.data_end_at.tzinfo is None:
                raise ValueError("data timestamps must include a timezone")
            if self.data_end_at <= self.data_start_at:
                raise ValueError("data_end_at must be after data_start_at")
        return self


class QualityEventRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_status: Literal["VALIDATED", "PUBLISHED", "QUARANTINED", "QUALITY_FAILED"]
    checks: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=2000)
    recorded_by: str = Field(min_length=1, max_length=100)


def _call(operation):
    try:
        return operation()
    except EAMIDataAssetError as exc:
        code = 404 if "not registered" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_data_asset(
    payload: DataAssetRegistration, request: Request
) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    return _call(lambda: register_data_asset(request.app.state.database, data))


@router.get("/{asset_id}")
def read_data_asset(asset_id: str, request: Request) -> dict[str, Any]:
    return _call(lambda: get_data_asset(request.app.state.database, asset_id))


@router.get("/{asset_id}/lineage")
def read_data_lineage(asset_id: str, request: Request) -> dict[str, Any]:
    return _call(lambda: get_data_lineage(request.app.state.database, asset_id))


@router.post("/{asset_id}/quality-events", status_code=status.HTTP_201_CREATED)
def create_quality_event(
    asset_id: str, payload: QualityEventRegistration, request: Request
) -> dict[str, Any]:
    return _call(
        lambda: record_asset_quality_event(
            request.app.state.database,
            {"asset_id": asset_id, **payload.model_dump()},
        )
    )
