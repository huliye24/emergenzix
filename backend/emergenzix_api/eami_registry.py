"""Immutable EAMI model registry and experiment ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .database import Database


class EAMIRegistryError(ValueError):
    """Raised when provenance or an append-only lifecycle transition is invalid."""


STAGE_TRANSITIONS: dict[str, set[str]] = {
    "REGISTERED": {"CHALLENGER", "REJECTED", "RETIRED"},
    "CHALLENGER": {"CHAMPION", "REJECTED", "RETIRED"},
    "CHAMPION": {"RETIRED"},
    "REJECTED": {"RETIRED"},
    "RETIRED": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EAMIRegistryError("record must contain finite JSON values") from exc


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _verified_record(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    payload = {field: row[field] for field in fields}
    for field in fields:
        if field.endswith("_json"):
            payload[field.removesuffix("_json")] = json.loads(payload.pop(field))
    if _fingerprint(payload) != row["record_sha256"]:
        raise EAMIRegistryError("stored EAMI record failed its integrity fingerprint")
    payload["record_sha256"] = row["record_sha256"]
    return payload


MODEL_FIELDS = (
    "model_name",
    "model_version",
    "model_family",
    "feature_version",
    "training_parameters_json",
    "code_sha256",
    "artifact_sha256",
    "parent_model_id",
    "created_by",
    "created_at",
)
EXPERIMENT_FIELDS = (
    "model_id",
    "dataset_id",
    "dataset_sha256",
    "input_asset_id",
    "output_asset_id",
    "data_start_at",
    "data_end_at",
    "protocol_version",
    "split_definition_json",
    "metrics_json",
    "outcome",
    "random_seed",
    "notes",
    "created_at",
)
STAGE_FIELDS = (
    "model_id",
    "experiment_id",
    "from_stage",
    "to_stage",
    "rationale",
    "recorded_by",
    "created_at",
)


def register_model(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    created_at = _now()
    record = {**payload, "created_at": created_at}
    model_id = _fingerprint(record)
    with db.connection() as connection:
        if payload.get("parent_model_id") is not None:
            parent = connection.execute(
                "SELECT id FROM eami_model_versions WHERE id = ?",
                (payload["parent_model_id"],),
            ).fetchone()
            if parent is None:
                raise EAMIRegistryError("parent model is not registered")
        try:
            connection.execute(
                """
                INSERT INTO eami_model_versions
                    (id, model_name, model_version, model_family, feature_version,
                     training_parameters_json, code_sha256, artifact_sha256,
                     parent_model_id, created_by, record_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    payload["model_name"],
                    payload["model_version"],
                    payload["model_family"],
                    payload["feature_version"],
                    _canonical_json(payload["training_parameters"]),
                    payload["code_sha256"],
                    payload.get("artifact_sha256"),
                    payload.get("parent_model_id"),
                    payload["created_by"],
                    model_id,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = connection.execute(
                "SELECT id FROM eami_model_versions WHERE model_name = ? AND model_version = ?",
                (payload["model_name"], payload["model_version"]),
            ).fetchone()
            if existing and existing["id"] == model_id:
                return get_model(db, model_id)
            raise EAMIRegistryError(
                "model name and version already identify different content"
            ) from exc

        stage_payload = {
            "model_id": model_id,
            "experiment_id": None,
            "from_stage": None,
            "to_stage": "REGISTERED",
            "rationale": "Immutable model version registered.",
            "recorded_by": payload["created_by"],
            "created_at": created_at,
        }
        stage_id = _fingerprint(stage_payload)
        connection.execute(
            """
            INSERT INTO eami_model_stage_events
                (id, model_id, experiment_id, from_stage, to_stage, rationale,
                 recorded_by, record_sha256, created_at)
            VALUES (?, ?, NULL, NULL, 'REGISTERED', ?, ?, ?, ?)
            """,
            (
                stage_id,
                model_id,
                stage_payload["rationale"],
                payload["created_by"],
                stage_id,
                created_at,
            ),
        )
    return get_model(db, model_id)


def get_model(db: Database, model_id: str) -> dict[str, Any]:
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM eami_model_versions WHERE id = ?", (model_id,)
        ).fetchone()
        if row is None:
            raise EAMIRegistryError("model is not registered")
        stage = connection.execute(
            """SELECT to_stage FROM eami_model_stage_events
               WHERE model_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (model_id,),
        ).fetchone()
    result = _verified_record(row, MODEL_FIELDS)
    return {
        "id": model_id,
        **result,
        "current_stage": stage["to_stage"],
        "automatic_promotion": False,
        "order_permission": False,
    }


def record_experiment(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    created_at = _now()
    with db.connection() as connection:
        model = connection.execute(
            "SELECT id FROM eami_model_versions WHERE id = ?", (payload["model_id"],)
        ).fetchone()
        if model is None:
            raise EAMIRegistryError("model is not registered")
        dataset_id = payload.get("dataset_id")
        input_asset_id = payload.get("input_asset_id")
        if (dataset_id is None) == (input_asset_id is None):
            raise EAMIRegistryError("provide exactly one dataset_id or input_asset_id")
        dataset_sha256 = None
        if dataset_id is not None:
            dataset = connection.execute(
                "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
            if dataset is None:
                raise EAMIRegistryError("dataset is not registered")
            if (
                payload["data_start_at"] < dataset["start_at"]
                or payload["data_end_at"] > dataset["end_at"]
            ):
                raise EAMIRegistryError(
                    "experiment range must be contained in the registered dataset"
                )
            dataset_sha256 = dataset["content_sha256"]
        else:
            asset = connection.execute(
                "SELECT * FROM eami_data_assets WHERE id = ?", (input_asset_id,)
            ).fetchone()
            if asset is None:
                raise EAMIRegistryError("input data asset is not registered")
            if (
                asset["data_start_at"]
                and payload["data_start_at"] < asset["data_start_at"]
            ) or (
                asset["data_end_at"] and payload["data_end_at"] > asset["data_end_at"]
            ):
                raise EAMIRegistryError(
                    "experiment range must be contained in the input asset"
                )
        output_asset_id = payload.get("output_asset_id")
        if output_asset_id is not None:
            output = connection.execute(
                "SELECT id FROM eami_data_assets WHERE id = ?", (output_asset_id,)
            ).fetchone()
            if output is None:
                raise EAMIRegistryError("output data asset is not registered")
        record = {
            **payload,
            "dataset_id": dataset_id,
            "dataset_sha256": dataset_sha256,
            "input_asset_id": input_asset_id,
            "output_asset_id": output_asset_id,
            "created_at": created_at,
        }
        experiment_id = _fingerprint(record)
        connection.execute(
            """
            INSERT OR IGNORE INTO eami_experiment_records
                (id, model_id, dataset_id, dataset_sha256, input_asset_id,
                 output_asset_id, data_start_at, data_end_at, protocol_version,
                 split_definition_json, metrics_json, outcome, random_seed,
                 notes, record_sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                payload["model_id"],
                dataset_id,
                dataset_sha256,
                input_asset_id,
                output_asset_id,
                payload["data_start_at"],
                payload["data_end_at"],
                payload["protocol_version"],
                _canonical_json(payload["split_definition"]),
                _canonical_json(payload["metrics"]),
                payload["outcome"],
                payload.get("random_seed"),
                payload.get("notes"),
                experiment_id,
                created_at,
            ),
        )
    return get_experiment(db, experiment_id)


def get_experiment(db: Database, experiment_id: str) -> dict[str, Any]:
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM eami_experiment_records WHERE id = ?", (experiment_id,)
        ).fetchone()
    if row is None:
        raise EAMIRegistryError("experiment is not recorded")
    return {
        "id": experiment_id,
        **_verified_record(row, EXPERIMENT_FIELDS),
        "automatic_promotion": False,
        "order_permission": False,
    }


def record_stage_event(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    created_at = _now()
    with db.connection() as connection:
        current = connection.execute(
            """SELECT to_stage FROM eami_model_stage_events
               WHERE model_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (payload["model_id"],),
        ).fetchone()
        if current is None:
            raise EAMIRegistryError("model is not registered")
        from_stage = current["to_stage"]
        to_stage = payload["to_stage"]
        if to_stage not in STAGE_TRANSITIONS[from_stage]:
            raise EAMIRegistryError(
                f"invalid model stage transition: {from_stage} -> {to_stage}"
            )
        experiment_id = payload.get("experiment_id")
        if to_stage in {"CHALLENGER", "CHAMPION"} and experiment_id is None:
            raise EAMIRegistryError(
                "challenger and champion stages require experiment evidence"
            )
        if experiment_id is not None:
            experiment = connection.execute(
                "SELECT model_id FROM eami_experiment_records WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if experiment is None or experiment["model_id"] != payload["model_id"]:
                raise EAMIRegistryError(
                    "stage evidence must be an experiment for the same model"
                )
        record = {
            **payload,
            "from_stage": from_stage,
            "created_at": created_at,
        }
        event_id = _fingerprint(record)
        connection.execute(
            """
            INSERT INTO eami_model_stage_events
                (id, model_id, experiment_id, from_stage, to_stage, rationale,
                 recorded_by, record_sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                payload["model_id"],
                experiment_id,
                from_stage,
                to_stage,
                payload["rationale"],
                payload["recorded_by"],
                event_id,
                created_at,
            ),
        )
    return {
        "id": event_id,
        **record,
        "record_sha256": event_id,
        "automatic_promotion": False,
        "order_permission": False,
    }
