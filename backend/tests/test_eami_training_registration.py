from __future__ import annotations

import hashlib
import json

import pytest

from emergenzix_api.database import Database
from emergenzix_api.eami_data_assets import register_data_asset
from emergenzix_api.eami_training_registration import (
    EAMITrainingRegistrationError,
    register_qlib_training_report,
)


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path):
    db = Database(str(tmp_path / "registry.db"))
    artifact = tmp_path / "qlib-lstm.pt"
    artifact.write_bytes(b"pinned model artifact")
    collection_sha = "c" * 64
    input_asset = register_data_asset(
        db,
        {
            "layer": "RAW",
            "asset_kind": "research_snapshot_set",
            "storage_uri": "s3://research/market-data/snapshot.json",
            "storage_format": "JSON",
            "schema_version": "snapshot-set-v1",
            "feature_version": None,
            "content_sha256": collection_sha,
            "producer_code_sha256": None,
            "source_asset_ids": [],
            "symbol": "BTCUSDT",
            "timeframe": "multi",
            "data_start_at": "2025-11-01T00:00:00+00:00",
            "data_end_at": "2026-11-02T00:00:00+00:00",
            "metadata": {},
            "created_by": "snapshot-publisher",
        },
    )
    report = {
        "dataset_collection_sha256": collection_sha,
        "protocol": "chronos-qlib-locked-comparison-v1",
        "safety": {
            "orders_created": False,
            "automatic_strategy_promotion": False,
            "paper_order_permission": False,
            "testnet_order_permission": False,
            "live_order_permission": False,
        },
        "qlib": {
            "seed": 20261103,
            "feature_names": ["return_1", "volume_zscore"],
            "sequence_length": 20,
            "train_end": "2026-06-30T00:00:00+00:00",
            "valid_start": "2026-07-01T00:00:00+00:00",
            "valid_end": "2026-08-31T00:00:00+00:00",
            "models": {
                "lstm": {
                    "artifact": str(artifact),
                    "artifact_sha256": _sha(artifact),
                    "epochs_observed": 4,
                }
            },
        },
        "metrics": {
            "qlib/lstm": {
                "overall": {
                    "direction_accuracy_pct": 51.0,
                    "cost_adjusted_sampled_return_sum_pct": -2.0,
                }
            }
        },
    }
    report_path = tmp_path / "training-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return db, input_asset, report_path


def test_training_report_registers_model_experiment_and_output_asset(tmp_path):
    db, input_asset, report_path = _setup(tmp_path)
    result = register_qlib_training_report(
        db=db,
        input_asset_id=input_asset["id"],
        report_path=report_path,
        code_sha256="a" * 64,
    )
    assert result["automatic_promotion"] is False
    assert result["order_permission"] is False
    assert len(result["models"]) == 1
    model = result["models"][0]["model"]
    experiment = result["models"][0]["experiment"]
    assert model["model_name"] == "qlib-lstm"
    assert model["current_stage"] == "REGISTERED"
    assert experiment["input_asset_id"] == input_asset["id"]
    assert experiment["output_asset_id"] == result["output_asset_id"]
    assert experiment["outcome"] == "INCONCLUSIVE"

    repeated = register_qlib_training_report(
        db=db,
        input_asset_id=input_asset["id"],
        report_path=report_path,
        code_sha256="a" * 64,
    )
    assert repeated["output_asset_id"] == result["output_asset_id"]
    assert repeated["models"][0]["experiment"]["id"] == experiment["id"]


def test_training_registration_rejects_wrong_input_or_permission(tmp_path):
    db, input_asset, report_path = _setup(tmp_path)
    report = json.loads(report_path.read_text())
    report["dataset_collection_sha256"] = "d" * 64
    report_path.write_text(json.dumps(report))
    with pytest.raises(EAMITrainingRegistrationError, match="does not match"):
        register_qlib_training_report(
            db=db,
            input_asset_id=input_asset["id"],
            report_path=report_path,
            code_sha256="a" * 64,
        )

    report["dataset_collection_sha256"] = input_asset["content_sha256"]
    report["safety"]["live_order_permission"] = True
    report_path.write_text(json.dumps(report))
    with pytest.raises(
        EAMITrainingRegistrationError, match="deny live_order_permission"
    ):
        register_qlib_training_report(
            db=db,
            input_asset_id=input_asset["id"],
            report_path=report_path,
            code_sha256="a" * 64,
        )
