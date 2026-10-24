"""API endpoints for immutable BTC point-in-time replay research records."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import Field

from .historical_replay import REPLAY_PROTOCOL_VERSION, run_historical_replay
from .schemas import Bar, StrictModel

router = APIRouter(
    prefix="/api/v1/research/historical-replays",
    tags=["research"],
)

_ALLOWED_BTC_QUOTES = {"USDT", "USDC", "USD", "FDUSD", "BUSD", "EUR"}


class HistoricalReplayCreate(StrictModel):
    """Run one reproducible replay against an already-registered dataset."""

    dataset_id: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    seed: int = Field(ge=0, le=2_147_483_647)
    horizon_bars: int = Field(default=12, ge=1, le=500)
    lookback: int = Field(default=20, ge=10, le=200)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_integrity_payload(record: dict[str, Any]) -> dict[str, Any]:
    # Creation time is metadata, not part of the reproducible experiment result.
    return {key: value for key, value in record.items() if key != "created_at"}


def _record_to_dict(row: Any) -> dict[str, Any]:
    record = json.loads(row["record_json"])
    actual_hash = _sha256(_canonical_json(_record_integrity_payload(record)))
    if actual_hash != row["record_sha256"]:
        raise HTTPException(
            status_code=500,
            detail="Stored historical replay record failed its integrity check",
        )
    return {**record, "record_sha256": row["record_sha256"]}


def _is_supported_btc_pair(symbol: str) -> bool:
    # Accept spot-style BTC/USDT and contract-style BTC/USDT:USDT labels, while
    # keeping this first research workflow deliberately limited to BTC quote pairs.
    base_pair = symbol.strip().upper().split(":", 1)[0]
    normalized = (
        base_pair.replace("/", "").replace("-", "").replace("_", "")
    )
    return normalized.startswith("BTC") and normalized[3:] in _ALLOWED_BTC_QUOTES


def _load_verified_dataset(row: Any) -> list[Bar]:
    bars_payload = json.loads(row["bars_json"])
    canonical_dataset = {
        "source": row["source"],
        "exchange": row["exchange"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "bars": bars_payload,
    }
    actual_hash = _sha256(_canonical_json(canonical_dataset))
    if row["id"] != row["content_sha256"] or actual_hash != row["content_sha256"]:
        raise HTTPException(
            status_code=409,
            detail="Registered dataset failed its content-integrity check",
        )
    try:
        return [Bar.model_validate(item) for item in bars_payload]
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Registered dataset contains invalid OHLCV bars",
        ) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_historical_replay(
    payload: HistoricalReplayCreate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Run and persist a BTC replay; this endpoint never creates orders."""
    db = request.app.state.database
    with db.connection() as connection:
        dataset = connection.execute(
            "SELECT * FROM market_datasets WHERE id = ?",
            (payload.dataset_id,),
        ).fetchone()

    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if not _is_supported_btc_pair(dataset["symbol"]):
        raise HTTPException(
            status_code=422,
            detail="Historical replay currently accepts BTC spot/quote datasets only",
        )

    bars = _load_verified_dataset(dataset)
    try:
        experiment = run_historical_replay(
            bars,
            seed=payload.seed,
            horizon_bars=payload.horizon_bars,
            lookback=payload.lookback,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    identity = {
        "dataset_id": dataset["id"],
        "dataset_sha256": dataset["content_sha256"],
        "protocol_version": REPLAY_PROTOCOL_VERSION,
        "seed": payload.seed,
        "horizon_bars": payload.horizon_bars,
        "lookback": payload.lookback,
    }
    record_id = _sha256(_canonical_json(identity))
    created_at = datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {
        "id": record_id,
        "record_type": "btc_historical_replay",
        "created_at": created_at,
        "protocol_version": REPLAY_PROTOCOL_VERSION,
        "dataset": {
            "id": dataset["id"],
            "sha256": dataset["content_sha256"],
            "source": dataset["source"],
            "exchange": dataset["exchange"],
            "symbol": dataset["symbol"],
            "timeframe": dataset["timeframe"],
            "start_at": dataset["start_at"],
            "end_at": dataset["end_at"],
            "bars_count": int(dataset["bars_count"]),
        },
        "configuration": {
            "seed": payload.seed,
            "horizon_bars": payload.horizon_bars,
            "lookback": payload.lookback,
        },
        "experiment": experiment.as_dict(),
        "safety": {
            "orders_created": False,
            "live_trading_enabled": False,
            "future_visible_during_analysis": False,
            "dataset_mutated": False,
        },
    }
    record_json = _canonical_json(record)
    record_sha256 = _sha256(_canonical_json(_record_integrity_payload(record)))

    with db.connection() as connection:
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO historical_replay_records
                (id, dataset_id, dataset_sha256, symbol, timeframe,
                 protocol_version, seed, horizon_bars, lookback, anchor_index,
                 record_sha256, record_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                dataset["id"],
                dataset["content_sha256"],
                dataset["symbol"],
                dataset["timeframe"],
                REPLAY_PROTOCOL_VERSION,
                payload.seed,
                payload.horizon_bars,
                payload.lookback,
                experiment.anchor_index,
                record_sha256,
                record_json,
                created_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM historical_replay_records WHERE id = ?",
            (record_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=500,
            detail="Historical replay record could not be persisted",
        )
    if row["record_sha256"] != record_sha256:
        raise HTTPException(
            status_code=409,
            detail=(
                "This dataset/configuration already has a different result under "
                "the current protocol version; increment the replay protocol version "
                "before changing replay semantics"
            ),
        )
    if inserted.rowcount == 0:
        response.status_code = status.HTTP_200_OK
    return _record_to_dict(row)


@router.get("")
def list_historical_replays(
    request: Request,
    dataset_id: str | None = Query(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """List immutable replay records, optionally filtered to one dataset."""
    db = request.app.state.database
    with db.connection() as connection:
        if dataset_id is None:
            rows = connection.execute(
                """
                SELECT * FROM historical_replay_records
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM historical_replay_records
                WHERE dataset_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (dataset_id, limit),
            ).fetchall()
    return [_record_to_dict(row) for row in rows]


@router.get("/{record_id}")
def get_historical_replay(record_id: str, request: Request) -> dict[str, Any]:
    """Retrieve one immutable replay record and verify its stored fingerprint."""
    db = request.app.state.database
    with db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM historical_replay_records WHERE id = ?",
            (record_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Historical replay record not found")
    return _record_to_dict(row)
