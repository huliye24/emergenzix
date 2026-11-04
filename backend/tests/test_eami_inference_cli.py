from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from emergenzix_api.database import Database
from emergenzix_api.eami_data_assets import (
    record_asset_quality_event,
    register_data_asset,
)
from emergenzix_api.eami_inference_cli import run_session
from emergenzix_api.eami_inference_ledger import create_inference_session
from emergenzix_api.eami_registry import register_model


def test_cli_backfills_gaps_labels_mature_predictions_and_resumes(tmp_path):
    path = tmp_path / "features.parquet"
    start = datetime(2026, 11, 3, tzinfo=timezone.utc)
    timestamps = [start + timedelta(hours=4 * index) for index in range(4)]
    pq.write_table(
        pa.table(
            {
                "timestamp": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
                "close": [100.0, 102.0, 101.0, 104.0],
                "return_1": [None, 0.02, -0.0098, 0.0297],
            }
        ),
        path,
    )
    db = Database(str(tmp_path / "ledger.db"))
    raw = register_data_asset(
        db,
        {
            "layer": "RAW",
            "asset_kind": "raw",
            "storage_uri": "file:///raw-source",
            "storage_format": "PARQUET",
            "schema_version": "v1",
            "feature_version": None,
            "content_sha256": "a" * 64,
            "producer_code_sha256": None,
            "source_asset_ids": [],
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "data_start_at": timestamps[0].isoformat(),
            "data_end_at": timestamps[-1].isoformat(),
            "metadata": {},
            "created_by": "test",
        },
    )
    feature = register_data_asset(
        db,
        {
            "layer": "FEATURE",
            "asset_kind": "return",
            "storage_uri": path.resolve().as_uri(),
            "storage_format": "PARQUET",
            "schema_version": "v1",
            "feature_version": "g1-return-v1",
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "producer_code_sha256": "b" * 64,
            "source_asset_ids": [raw["id"]],
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "data_start_at": timestamps[0].isoformat(),
            "data_end_at": timestamps[-1].isoformat(),
            "metadata": {},
            "created_by": "test",
        },
    )
    for state in ("VALIDATED", "PUBLISHED"):
        record_asset_quality_event(
            db,
            {
                "asset_id": feature["id"],
                "to_status": state,
                "checks": {"hash": "PASS"},
                "rationale": "test",
                "recorded_by": "test",
            },
        )
    model = register_model(
        db,
        {
            "model_name": "g1-return",
            "model_version": "1",
            "model_family": "baseline_return",
            "feature_version": "g1-return-v1",
            "training_parameters": {},
            "code_sha256": "c" * 64,
            "artifact_sha256": None,
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
            "first_expected_at": timestamps[0].isoformat(),
        },
    )
    session_id = session["session"]["id"]
    first = run_session(
        db=db,
        session_id=session_id,
        through_at=timestamps[2].isoformat(),
        observed_at=(timestamps[2] + timedelta(hours=4)).isoformat(),
    )
    assert first == {
        "session_id": session_id,
        "predictions": 2,
        "failures": 1,
        "outcomes": 1,
        "remaining_gaps": 0,
        "order_permission": False,
    }
    repeated = run_session(
        db=db,
        session_id=session_id,
        through_at=timestamps[2].isoformat(),
        observed_at=(timestamps[2] + timedelta(hours=4)).isoformat(),
    )
    assert repeated["predictions"] == 0 and repeated["failures"] == 0
