"""Register a completed Qlib training report in the immutable EAMI ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .database import Database
from .eami_data_assets import get_data_asset, register_data_asset
from .eami_registry import get_experiment, get_model, record_experiment, register_model


class EAMITrainingRegistrationError(ValueError):
    """Raised when a training output cannot be proven against its input asset."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _existing_model(db: Database, name: str, version: str) -> dict[str, Any] | None:
    with db.connection() as connection:
        row = connection.execute(
            "SELECT id FROM eami_model_versions WHERE model_name = ? AND model_version = ?",
            (name, version),
        ).fetchone()
    return None if row is None else get_model(db, row["id"])


def _existing_output_asset(
    db: Database, storage_uri: str, content_sha256: str
) -> dict[str, Any] | None:
    with db.connection() as connection:
        row = connection.execute(
            "SELECT id FROM eami_data_assets WHERE storage_uri = ? AND content_sha256 = ?",
            (storage_uri, content_sha256),
        ).fetchone()
    return None if row is None else get_data_asset(db, row["id"])


def register_qlib_training_report(
    *, db: Database, input_asset_id: str, report_path: Path, code_sha256: str
) -> dict[str, Any]:
    input_asset = get_data_asset(db, input_asset_id)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("dataset_collection_sha256") != input_asset["content_sha256"]:
        raise EAMITrainingRegistrationError(
            "training report dataset hash does not match the pinned input asset"
        )
    safety = report.get("safety", {})
    for permission in (
        "orders_created",
        "automatic_strategy_promotion",
        "paper_order_permission",
        "testnet_order_permission",
        "live_order_permission",
    ):
        if safety.get(permission) is not False:
            raise EAMITrainingRegistrationError(
                f"training report must explicitly deny {permission}"
            )
    qlib = report.get("qlib")
    if not isinstance(qlib, dict) or not qlib.get("models"):
        raise EAMITrainingRegistrationError("training report contains no Qlib models")

    report_sha = _sha256(report_path)
    report_uri = report_path.resolve().as_uri()
    output_asset = _existing_output_asset(db, report_uri, report_sha)
    if output_asset is None:
        output_asset = register_data_asset(
            db,
            {
                "layer": "RESEARCH_MEMORY",
                "asset_kind": "qlib_training_report",
                "storage_uri": report_uri,
                "storage_format": "JSON",
                "schema_version": report.get("protocol", "qlib-report-v1"),
                "feature_version": None,
                "content_sha256": report_sha,
                "producer_code_sha256": code_sha256,
                "source_asset_ids": [input_asset_id],
                "symbol": input_asset.get("symbol"),
                "timeframe": input_asset.get("timeframe"),
                "data_start_at": input_asset.get("data_start_at"),
                "data_end_at": input_asset.get("data_end_at"),
                "metadata": {"model_families": sorted(qlib["models"])},
                "created_by": "aws-gpu-training",
            },
        )

    run_version = report_sha[:16]
    registered = []
    for name, metadata in sorted(qlib["models"].items()):
        artifact = Path(metadata["artifact"])
        if not artifact.is_file() or _sha256(artifact) != metadata["artifact_sha256"]:
            raise EAMITrainingRegistrationError(
                f"model artifact failed integrity verification: {name}"
            )
        model_name = f"qlib-{name}"
        model = _existing_model(db, model_name, run_version)
        if model is None:
            model = register_model(
                db,
                {
                    "model_name": model_name,
                    "model_version": run_version,
                    "model_family": f"qlib/{name}",
                    "feature_version": "qlib-locked-features-v1",
                    "training_parameters": {
                        "seed": qlib.get("seed"),
                        "feature_names": qlib.get("feature_names"),
                        "sequence_length": qlib.get("sequence_length"),
                        "train_end": qlib.get("train_end"),
                        "valid_start": qlib.get("valid_start"),
                        "valid_end": qlib.get("valid_end"),
                    },
                    "code_sha256": code_sha256,
                    "artifact_sha256": metadata["artifact_sha256"],
                    "parent_model_id": None,
                    "created_by": "aws-gpu-training",
                },
            )
        with db.connection() as connection:
            existing = connection.execute(
                """SELECT id FROM eami_experiment_records
                   WHERE model_id = ? AND input_asset_id = ? AND output_asset_id = ?
                   AND protocol_version = ?""",
                (model["id"], input_asset_id, output_asset["id"], report["protocol"]),
            ).fetchone()
        experiment = (
            get_experiment(db, existing["id"])
            if existing is not None
            else record_experiment(
                db,
                {
                    "model_id": model["id"],
                    "dataset_id": None,
                    "input_asset_id": input_asset_id,
                    "output_asset_id": output_asset["id"],
                    "data_start_at": input_asset["data_start_at"],
                    "data_end_at": input_asset["data_end_at"],
                    "protocol_version": report["protocol"],
                    "split_definition": {
                        "train_end": qlib.get("train_end"),
                        "valid_start": qlib.get("valid_start"),
                        "valid_end": qlib.get("valid_end"),
                    },
                    "metrics": report["metrics"][f"qlib/{name}"],
                    "outcome": "INCONCLUSIVE",
                    "random_seed": qlib.get("seed"),
                    "notes": "Registration records evidence only; no automatic promotion.",
                },
            )
        )
        registered.append({"model": model, "experiment": experiment})
    return {
        "input_asset_id": input_asset_id,
        "output_asset_id": output_asset["id"],
        "models": registered,
        "automatic_promotion": False,
        "order_permission": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--input-asset-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--code-sha256", required=True)
    args = parser.parse_args()
    result = register_qlib_training_report(
        db=Database(str(args.db)),
        input_asset_id=args.input_asset_id,
        report_path=args.report,
        code_sha256=args.code_sha256,
    )
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
