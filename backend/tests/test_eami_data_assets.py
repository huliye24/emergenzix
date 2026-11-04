from __future__ import annotations

import sqlite3

import pytest

from emergenzix_api.database import Database
from emergenzix_api.eami_data_assets import (
    EAMIDataAssetError,
    get_data_asset,
    get_data_lineage,
    register_data_asset,
)
from emergenzix_api.main import create_app


def _asset(layer: str, suffix: str, **overrides) -> dict:
    payload = {
        "layer": layer,
        "asset_kind": "binance_market_data",
        "storage_uri": f"s3://research/market-data/{suffix}.parquet",
        "storage_format": "PARQUET",
        "schema_version": "market-v2",
        "feature_version": None,
        "content_sha256": suffix[0] * 64,
        "producer_code_sha256": None,
        "source_asset_ids": [],
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "data_start_at": "2026-01-01T00:00:00+00:00",
        "data_end_at": "2026-02-01T00:00:00+00:00",
        "metadata": {"provider": "binance"},
        "created_by": "data-ingestion",
    }
    payload.update(overrides)
    return payload


def test_cross_generation_asset_lineage_is_replayable(tmp_path):
    db = Database(str(tmp_path / "assets.db"))
    raw = register_data_asset(db, _asset("RAW", "a-raw"))
    canonical = register_data_asset(
        db,
        _asset(
            "CANONICAL",
            "b-canonical",
            source_asset_ids=[raw["id"]],
            producer_code_sha256="1" * 64,
        ),
    )
    feature = register_data_asset(
        db,
        _asset(
            "FEATURE",
            "c-feature",
            asset_kind="market_flow_features",
            feature_version="flow-v1",
            source_asset_ids=[canonical["id"]],
            producer_code_sha256="2" * 64,
        ),
    )

    lineage = get_data_lineage(db, feature["id"])
    assert [item["layer"] for item in lineage["assets"]] == [
        "RAW",
        "CANONICAL",
        "FEATURE",
    ]
    assert lineage["replayable"] is True
    assert lineage["order_permission"] is False
    assert get_data_asset(db, feature["id"])["feature_version"] == "flow-v1"


def test_lineage_deduplicates_a_shared_ancestor(tmp_path):
    db = Database(str(tmp_path / "dag.db"))
    raw = register_data_asset(db, _asset("RAW", "a-raw"))
    left = register_data_asset(
        db,
        _asset(
            "CANONICAL",
            "b-left",
            source_asset_ids=[raw["id"]],
            producer_code_sha256="1" * 64,
        ),
    )
    right = register_data_asset(
        db,
        _asset(
            "CANONICAL",
            "c-right",
            source_asset_ids=[raw["id"]],
            producer_code_sha256="2" * 64,
        ),
    )
    memory = register_data_asset(
        db,
        _asset(
            "RESEARCH_MEMORY",
            "d-memory",
            source_asset_ids=[left["id"], right["id"]],
            producer_code_sha256="3" * 64,
        ),
    )
    lineage = get_data_lineage(db, memory["id"])
    assert [item["id"] for item in lineage["assets"]].count(raw["id"]) == 1


def test_raw_and_derived_provenance_rules_fail_closed(tmp_path):
    db = Database(str(tmp_path / "rules.db"))
    with pytest.raises(EAMIDataAssetError, match="RAW assets cannot"):
        register_data_asset(
            db,
            _asset("RAW", "a-raw", producer_code_sha256="1" * 64),
        )
    with pytest.raises(EAMIDataAssetError, match="require source assets"):
        register_data_asset(db, _asset("CANONICAL", "b-canonical"))
    with pytest.raises(EAMIDataAssetError, match="feature_version"):
        register_data_asset(
            db,
            _asset(
                "FEATURE",
                "c-feature",
                source_asset_ids=["a" * 64],
                producer_code_sha256="1" * 64,
            ),
        )


def test_missing_parent_and_credential_bearing_uri_are_rejected(tmp_path):
    db = Database(str(tmp_path / "invalid.db"))
    with pytest.raises(EAMIDataAssetError, match="already be registered"):
        register_data_asset(
            db,
            _asset(
                "CANONICAL",
                "b-canonical",
                source_asset_ids=["a" * 64],
                producer_code_sha256="1" * 64,
            ),
        )
    with pytest.raises(EAMIDataAssetError, match="credentials"):
        register_data_asset(
            db,
            _asset("RAW", "a-raw", storage_uri="https://user:secret@example.com/raw"),
        )


def test_data_assets_cannot_be_updated_or_deleted(tmp_path):
    db = Database(str(tmp_path / "immutable.db"))
    asset = register_data_asset(db, _asset("RAW", "a-raw"))
    with db.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE eami_data_assets SET schema_version = 'changed' WHERE id = ?",
                (asset["id"],),
            )
    with db.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM eami_data_assets WHERE id = ?", (asset["id"],)
            )


def test_data_asset_routes_are_read_write_catalog_only(tmp_path):
    app = create_app(db_path=tmp_path / "routes.db")
    routes = {
        (route.path, method) for route in app.routes for method in route.methods or []
    }
    assert ("/api/v1/eami/data-assets", "POST") in routes
    assert ("/api/v1/eami/data-assets/{asset_id}", "GET") in routes
    assert ("/api/v1/eami/data-assets/{asset_id}/lineage", "GET") in routes
    assert not any(
        "order" in path or "train" in path
        for path, _ in routes
        if "/eami/data-assets" in path
    )
