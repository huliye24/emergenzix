"""Model-independent, immutable data inheritance catalog for EAMI."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .database import Database


class EAMIDataAssetError(ValueError):
    """Raised when a data asset violates provenance or integrity rules."""


QUALITY_TRANSITIONS = {
    "DRAFT": {"VALIDATED", "QUARANTINED", "QUALITY_FAILED"},
    "VALIDATED": {"PUBLISHED", "QUARANTINED", "QUALITY_FAILED"},
    "PUBLISHED": {"QUARANTINED"},
    "QUARANTINED": {"VALIDATED", "QUALITY_FAILED"},
    "QUALITY_FAILED": {"VALIDATED"},
}


ASSET_FIELDS = (
    "layer",
    "asset_kind",
    "storage_uri",
    "storage_format",
    "schema_version",
    "feature_version",
    "content_sha256",
    "producer_code_sha256",
    "source_asset_ids_json",
    "symbol",
    "timeframe",
    "data_start_at",
    "data_end_at",
    "metadata_json",
    "created_by",
    "created_at",
)


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
        raise EAMIDataAssetError(
            "asset metadata must contain finite JSON values"
        ) from exc


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_storage_uri(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"s3", "file", "https"}:
        raise EAMIDataAssetError("storage_uri must use s3, file, or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EAMIDataAssetError(
            "storage_uri must not contain credentials, query, or fragment"
        )
    if not parsed.netloc and parsed.scheme != "file":
        raise EAMIDataAssetError("storage_uri must identify a storage authority")


def register_data_asset(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    _safe_storage_uri(payload["storage_uri"])
    layer = payload["layer"]
    sources = payload["source_asset_ids"]
    producer = payload.get("producer_code_sha256")
    feature_version = payload.get("feature_version")
    if len(sources) != len(set(sources)):
        raise EAMIDataAssetError("source_asset_ids must be unique")
    if layer == "RAW":
        if sources or producer is not None or feature_version is not None:
            raise EAMIDataAssetError(
                "RAW assets cannot claim parents, producer code, or feature versions"
            )
    else:
        if not sources or producer is None:
            raise EAMIDataAssetError(
                "derived assets require source assets and producer code provenance"
            )
    if layer == "FEATURE" and feature_version is None:
        raise EAMIDataAssetError("FEATURE assets require feature_version")
    if layer != "FEATURE" and feature_version is not None:
        raise EAMIDataAssetError("feature_version is only valid for FEATURE assets")

    created_at = _now()
    record = {**payload, "created_at": created_at}
    asset_id = _fingerprint(record)
    with db.connection() as connection:
        if sources:
            placeholders = ",".join("?" for _ in sources)
            rows = connection.execute(
                f"SELECT id FROM eami_data_assets WHERE id IN ({placeholders})", sources
            ).fetchall()
            found = {row["id"] for row in rows}
            missing = [source for source in sources if source not in found]
            if missing:
                raise EAMIDataAssetError("all source assets must already be registered")
        try:
            connection.execute(
                """
                INSERT INTO eami_data_assets
                    (id, layer, asset_kind, storage_uri, storage_format,
                     schema_version, feature_version, content_sha256,
                     producer_code_sha256, source_asset_ids_json, symbol,
                     timeframe, data_start_at, data_end_at, metadata_json,
                     created_by, record_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    layer,
                    payload["asset_kind"],
                    payload["storage_uri"],
                    payload["storage_format"],
                    payload["schema_version"],
                    feature_version,
                    payload["content_sha256"],
                    producer,
                    _canonical_json(sources),
                    payload.get("symbol"),
                    payload.get("timeframe"),
                    payload.get("data_start_at"),
                    payload.get("data_end_at"),
                    _canonical_json(payload["metadata"]),
                    payload["created_by"],
                    asset_id,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise EAMIDataAssetError(
                "the storage URI and content hash are already registered"
            ) from exc
        quality = {
            "asset_id": asset_id,
            "from_status": None,
            "to_status": "DRAFT",
            "checks": {},
            "rationale": "Asset registered; quality review has not completed.",
            "recorded_by": payload["created_by"],
            "created_at": created_at,
        }
        quality_id = _fingerprint(quality)
        connection.execute(
            """INSERT INTO eami_data_asset_quality_events
               (id, asset_id, from_status, to_status, checks_json, rationale,
                recorded_by, record_sha256, created_at)
               VALUES (?, ?, NULL, 'DRAFT', '{}', ?, ?, ?, ?)""",
            (
                quality_id,
                asset_id,
                quality["rationale"],
                payload["created_by"],
                quality_id,
                created_at,
            ),
        )
    return get_data_asset(db, asset_id)


def get_data_asset(db: Database, asset_id: str) -> dict[str, Any]:
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM eami_data_assets WHERE id = ?", (asset_id,)
        ).fetchone()
    if row is None:
        raise EAMIDataAssetError("data asset is not registered")
    payload = {field: row[field] for field in ASSET_FIELDS}
    payload["source_asset_ids"] = json.loads(payload.pop("source_asset_ids_json"))
    payload["metadata"] = json.loads(payload.pop("metadata_json"))
    if _fingerprint(payload) != row["record_sha256"]:
        raise EAMIDataAssetError("stored data asset failed its integrity fingerprint")
    return {
        "id": asset_id,
        **payload,
        "record_sha256": row["record_sha256"],
        "quality_status": get_asset_quality_status(db, asset_id),
    }


def get_asset_quality_status(db: Database, asset_id: str) -> str:
    with db.connection() as connection:
        row = connection.execute(
            """SELECT to_status FROM eami_data_asset_quality_events
               WHERE asset_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (asset_id,),
        ).fetchone()
    if row is None:
        raise EAMIDataAssetError("data asset has no quality history")
    return str(row["to_status"])


def record_asset_quality_event(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    created_at = _now()
    with db.connection() as connection:
        current = connection.execute(
            """SELECT to_status FROM eami_data_asset_quality_events
               WHERE asset_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (payload["asset_id"],),
        ).fetchone()
        if current is None:
            raise EAMIDataAssetError("data asset is not registered")
        from_status = str(current["to_status"])
        to_status = payload["to_status"]
        if to_status not in QUALITY_TRANSITIONS[from_status]:
            raise EAMIDataAssetError(
                f"invalid asset quality transition: {from_status} -> {to_status}"
            )
        if to_status in {"VALIDATED", "PUBLISHED"} and not payload["checks"]:
            raise EAMIDataAssetError(
                "validated and published states require quality checks"
            )
        record = {**payload, "from_status": from_status, "created_at": created_at}
        event_id = _fingerprint(record)
        connection.execute(
            """INSERT INTO eami_data_asset_quality_events
               (id, asset_id, from_status, to_status, checks_json, rationale,
                recorded_by, record_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                payload["asset_id"],
                from_status,
                to_status,
                _canonical_json(payload["checks"]),
                payload["rationale"],
                payload["recorded_by"],
                event_id,
                created_at,
            ),
        )
    return {"id": event_id, **record, "record_sha256": event_id}


def get_data_lineage(db: Database, asset_id: str) -> dict[str, Any]:
    root = get_data_asset(db, asset_id)
    ordered: list[dict[str, Any]] = []
    active: set[str] = set()
    completed: set[str] = set()

    def visit(current: dict[str, Any]) -> None:
        if current["id"] in completed:
            return
        if current["id"] in active:
            raise EAMIDataAssetError("data lineage contains a cycle")
        active.add(current["id"])
        for parent_id in current["source_asset_ids"]:
            visit(get_data_asset(db, parent_id))
        active.remove(current["id"])
        completed.add(current["id"])
        ordered.append(current)

    visit(root)
    return {
        "asset_id": asset_id,
        "assets": ordered,
        "replayable": all(
            item["layer"] == "RAW" or item["producer_code_sha256"] is not None
            for item in ordered
        ),
        "order_permission": False,
    }
