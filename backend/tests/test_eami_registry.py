from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from emergenzix_api.database import Database
from emergenzix_api.eami_registry import (
    EAMIRegistryError,
    get_experiment,
    get_model,
    record_experiment,
    record_stage_event,
    register_model,
)
from emergenzix_api.main import create_app


SHA_A = "a" * 64
SHA_B = "b" * 64
START = "2026-01-01T00:00:00+00:00"
END = "2026-01-02T00:00:00+00:00"


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "eami.db"))
    bars_json = json.dumps([{"timestamp": START}, {"timestamp": END}])
    dataset_sha = hashlib.sha256(bars_json.encode()).hexdigest()
    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO market_datasets
                (id, content_sha256, source, exchange, symbol, timeframe,
                 start_at, end_at, bars_count, bars_json, created_at)
            VALUES (?, ?, 'synthetic_demo', NULL, 'BTCUSDT', '4h', ?, ?, 5, ?, ?)
            """,
            ("d" * 64, dataset_sha, START, END, bars_json, START),
        )
    return database


def _model_payload(**overrides) -> dict:
    payload = {
        "model_name": "eami-hmm",
        "model_version": "1.0.0",
        "model_family": "hidden_markov_model",
        "feature_version": "market-flow-v1",
        "training_parameters": {"states": 4, "covariance": "full"},
        "code_sha256": SHA_A,
        "artifact_sha256": SHA_B,
        "parent_model_id": None,
        "created_by": "research-operator",
    }
    payload.update(overrides)
    return payload


def _experiment_payload(model_id: str, **overrides) -> dict:
    payload = {
        "model_id": model_id,
        "dataset_id": "d" * 64,
        "data_start_at": START,
        "data_end_at": END,
        "protocol_version": "walk-forward-v1",
        "split_definition": {"method": "purged_walk_forward", "folds": 3},
        "metrics": {"log_loss": 0.61, "brier": 0.19},
        "outcome": "PASSED",
        "random_seed": 7,
        "notes": None,
    }
    payload.update(overrides)
    return payload


def test_model_experiment_and_stage_are_append_only_and_never_grant_orders(db):
    model = register_model(db, _model_payload())
    assert len(model["id"]) == 64
    assert model["current_stage"] == "REGISTERED"
    assert model["training_parameters"]["states"] == 4
    assert model["automatic_promotion"] is False
    assert model["order_permission"] is False

    experiment = record_experiment(db, _experiment_payload(model["id"]))
    assert len(experiment["dataset_sha256"]) == 64
    assert experiment["order_permission"] is False
    assert experiment["automatic_promotion"] is False

    event = record_stage_event(
        db,
        {
            "model_id": model["id"],
            "experiment_id": experiment["id"],
            "to_stage": "CHALLENGER",
            "rationale": "Passed offline protocol; PAPER evidence remains required.",
            "recorded_by": "research-reviewer",
        },
    )
    assert event["from_stage"] == "REGISTERED"
    assert event["order_permission"] is False
    assert get_model(db, model["id"])["current_stage"] == "CHALLENGER"
    assert get_experiment(db, experiment["id"])["metrics"]["brier"] == 0.19


def test_duplicate_version_with_different_content_is_rejected(db):
    register_model(db, _model_payload())
    with pytest.raises(EAMIRegistryError, match="already identify different content"):
        register_model(db, _model_payload(training_parameters={"states": 9}))


def test_promotion_requires_same_model_experiment_evidence(db):
    first = register_model(db, _model_payload())
    second = register_model(
        db, _model_payload(model_name="eami-gmm", model_version="0.1.0")
    )
    with pytest.raises(EAMIRegistryError, match="require experiment evidence"):
        record_stage_event(
            db,
            {
                "model_id": first["id"],
                "experiment_id": None,
                "to_stage": "CHALLENGER",
                "rationale": "No evidence supplied.",
                "recorded_by": "reviewer",
            },
        )

    experiment = record_experiment(db, _experiment_payload(second["id"]))
    with pytest.raises(EAMIRegistryError, match="same model"):
        record_stage_event(
            db,
            {
                "model_id": first["id"],
                "experiment_id": experiment["id"],
                "to_stage": "CHALLENGER",
                "rationale": "Wrong model evidence.",
                "recorded_by": "reviewer",
            },
        )


def test_registry_tables_block_update_and_delete(db):
    model = register_model(db, _model_payload())
    with db.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE eami_model_versions SET feature_version = 'changed' WHERE id = ?",
                (model["id"],),
            )
    with db.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM eami_model_stage_events WHERE model_id = ?",
                (model["id"],),
            )


def test_experiment_range_must_be_inside_registered_dataset(db):
    model = register_model(db, _model_payload())
    with pytest.raises(EAMIRegistryError, match="contained"):
        record_experiment(
            db,
            _experiment_payload(model["id"], data_start_at="2025-12-31T00:00:00+00:00"),
        )


def test_routes_are_registered_without_order_or_auto_promotion_actions(tmp_path):
    app = create_app(db_path=tmp_path / "routes.db")
    routes = {
        (route.path, method) for route in app.routes for method in route.methods or []
    }
    assert ("/api/v1/eami/models", "POST") in routes
    assert ("/api/v1/eami/models/{model_id}", "GET") in routes
    assert ("/api/v1/eami/experiments", "POST") in routes
    assert ("/api/v1/eami/stage-events", "POST") in routes
    assert not any(
        "order" in path or "auto-promote" in path
        for path, _ in routes
        if "/eami/" in path
    )
