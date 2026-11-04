from __future__ import annotations

import pytest

from emergenzix_api.database import Database
from emergenzix_api.eami_data_assets import register_data_asset
from emergenzix_api.eami_inference_ledger import (
    EAMIInferenceError,
    create_inference_session,
    get_inference_status,
    record_observation,
    record_outcome,
)
from emergenzix_api.eami_registry import register_model


def _setup(tmp_path):
    db = Database(str(tmp_path / "inference.db"))
    raw = register_data_asset(
        db,
        {
            "layer": "RAW",
            "asset_kind": "raw",
            "storage_uri": "s3://research/raw",
            "storage_format": "PARQUET",
            "schema_version": "v1",
            "feature_version": None,
            "content_sha256": "a" * 64,
            "producer_code_sha256": None,
            "source_asset_ids": [],
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "data_start_at": "2026-11-01T00:00:00+00:00",
            "data_end_at": "2026-11-04T00:00:00+00:00",
            "metadata": {},
            "created_by": "test",
        },
    )
    feature = register_data_asset(
        db,
        {
            "layer": "FEATURE",
            "asset_kind": "flow",
            "storage_uri": "s3://research/feature",
            "storage_format": "PARQUET",
            "schema_version": "v1",
            "feature_version": "flow-v1",
            "content_sha256": "b" * 64,
            "producer_code_sha256": "c" * 64,
            "source_asset_ids": [raw["id"]],
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "data_start_at": "2026-11-01T00:00:00+00:00",
            "data_end_at": "2026-11-04T00:00:00+00:00",
            "metadata": {},
            "created_by": "test",
        },
    )
    model = register_model(
        db,
        {
            "model_name": "g1",
            "model_version": "1",
            "model_family": "baseline",
            "feature_version": "flow-v1",
            "training_parameters": {},
            "code_sha256": "d" * 64,
            "artifact_sha256": "e" * 64,
            "parent_model_id": None,
            "created_by": "test",
        },
    )
    session = create_inference_session(
        db,
        {
            "model_id": model["id"],
            "input_asset_id": feature["id"],
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "horizon_bars": 1,
            "first_expected_at": "2026-11-03T00:00:00+00:00",
        },
    )
    return db, session["session"]["id"]


def test_session_resumes_and_fills_prediction_or_failure_once(tmp_path):
    db, session_id = _setup(tmp_path)
    first = record_observation(
        db,
        {
            "session_id": session_id,
            "completed_at": "2026-11-03T00:00:00+00:00",
            "status": "PREDICTION",
            "input_sha256": "f" * 64,
            "direction": "UP",
            "score": 0.6,
            "prediction": {"return": 0.01},
            "error_code": None,
        },
    )
    record_observation(
        db,
        {
            "session_id": session_id,
            "completed_at": "2026-11-03T04:00:00+00:00",
            "status": "FAILURE",
            "input_sha256": None,
            "direction": None,
            "score": None,
            "prediction": {},
            "error_code": "MODEL_LOAD_FAILED",
        },
    )
    status = get_inference_status(
        db, session_id, through_at="2026-11-03T12:00:00+00:00"
    )
    assert status["checkpoint_completed_at"] == "2026-11-03T04:00:00+00:00"
    assert status["missing_expected_at"] == [
        "2026-11-03T08:00:00+00:00",
        "2026-11-03T12:00:00+00:00",
    ]
    assert status["prediction_count"] == 1 and status["failure_count"] == 1
    with pytest.raises(EAMIInferenceError, match="already recorded"):
        record_observation(db, {**first, "session_id": session_id})


def test_outcome_is_blocked_until_horizon_and_is_append_only(tmp_path):
    db, session_id = _setup(tmp_path)
    prediction = record_observation(
        db,
        {
            "session_id": session_id,
            "completed_at": "2026-11-03T00:00:00+00:00",
            "status": "PREDICTION",
            "input_sha256": "f" * 64,
            "direction": "DOWN",
            "score": 0.55,
            "prediction": {},
            "error_code": None,
        },
    )
    with pytest.raises(EAMIInferenceError, match="before"):
        record_outcome(
            db,
            prediction["id"],
            label="DOWN",
            actual_return=-0.01,
            outcome={},
            observed_at="2026-11-03T07:59:00+00:00",
        )
    record_outcome(
        db,
        prediction["id"],
        label="DOWN",
        actual_return=-0.01,
        outcome={"close": 70000},
        observed_at="2026-11-03T08:00:00+00:00",
    )
    with pytest.raises(EAMIInferenceError, match="already recorded"):
        record_outcome(
            db,
            prediction["id"],
            label="UP",
            actual_return=0.01,
            outcome={},
            observed_at="2026-11-03T09:00:00+00:00",
        )


def test_session_definition_is_immutable(tmp_path):
    db, session_id = _setup(tmp_path)
    assert len(session_id) == 64
    with db.connection() as connection:
        with pytest.raises(Exception, match="immutable"):
            connection.execute("UPDATE eami_inference_sessions SET timeframe='1h'")
