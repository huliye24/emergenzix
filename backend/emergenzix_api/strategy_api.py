"""Strategy CRUD and immutable parameter version API.

All database access uses the current app's explicit request state; no orders,
research execution, exchange credentials or global database singleton.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from .api_common import _now, _row_to_dict
from .database import Database
from .schemas import StrategyCreate, StrategyVersionCreate

router = APIRouter()


def _canonical_strategy_parameters_json(
    strategy_type: str, fast_window: int, slow_window: int
) -> str:
    return json.dumps(
        {
            "strategy_type": strategy_type,
            "fast_window": int(fast_window),
            "slow_window": int(slow_window),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _strategy_version_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "strategy_id": row["strategy_id"],
        "version": int(row["version"]),
        "strategy_type": row["strategy_type"],
        "parameters": json.loads(row["parameters_json"]),
        "parameters_sha256": row["parameters_sha256"],
        "created_at": row["created_at"],
    }


def _strategy_with_current_version(connection: Any, strategy_id: str) -> Any:
    return connection.execute(
        """
        SELECT s.*, v.id AS current_version_id, v.version AS current_version,
               v.parameters_sha256 AS current_parameters_sha256
        FROM strategies AS s
        LEFT JOIN strategy_versions AS v
          ON v.strategy_id = s.id
         AND v.version = (
             SELECT MAX(v2.version)
             FROM strategy_versions AS v2
             WHERE v2.strategy_id = s.id
         )
        WHERE s.id = ?
        """,
        (strategy_id,),
    ).fetchone()


@router.get("/api/v1/strategies")
def list_strategies(request: Request) -> list[dict[str, Any]]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        rows = connection.execute(
            """
            SELECT s.*, v.id AS current_version_id,
                   v.version AS current_version,
                   v.parameters_sha256 AS current_parameters_sha256
            FROM strategies AS s
            LEFT JOIN strategy_versions AS v
              ON v.strategy_id = s.id
             AND v.version = (
                 SELECT MAX(v2.version)
                 FROM strategy_versions AS v2
                 WHERE v2.strategy_id = s.id
             )
            ORDER BY s.created_at DESC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]

@router.post("/api/v1/strategies", status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreate, request: Request
) -> dict[str, Any]:
    db: Database = request.app.state.database
    strategy_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = _now()
    parameters_json = _canonical_strategy_parameters_json(
        payload.strategy_type, payload.fast_window, payload.slow_window
    )
    parameters_sha256 = hashlib.sha256(
        parameters_json.encode("utf-8")
    ).hexdigest()
    with db.connection() as connection:
        connection.execute(
            """
            INSERT INTO strategies
                (id, name, symbol, strategy_type, fast_window, slow_window,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                strategy_id,
                payload.name.strip(),
                payload.symbol,
                payload.strategy_type,
                payload.fast_window,
                payload.slow_window,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO strategy_versions
                (id, strategy_id, version, strategy_type, fast_window,
                 slow_window, parameters_json, parameters_sha256, created_at)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                strategy_id,
                payload.strategy_type,
                payload.fast_window,
                payload.slow_window,
                parameters_json,
                parameters_sha256,
                now,
            ),
        )
        row = _strategy_with_current_version(connection, strategy_id)
    return _row_to_dict(row)

@router.get("/api/v1/strategies/{strategy_id}/versions")
def list_strategy_versions(
    strategy_id: str, request: Request
) -> list[dict[str, Any]]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        strategy = connection.execute(
            "SELECT id FROM strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
        if strategy is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        rows = connection.execute(
            """
            SELECT * FROM strategy_versions
            WHERE strategy_id = ?
            ORDER BY version ASC
            """,
            (strategy_id,),
        ).fetchall()
    return [_strategy_version_to_dict(row) for row in rows]

@router.post(
    "/api/v1/strategies/{strategy_id}/versions",
    status_code=status.HTTP_201_CREATED,
)
def create_strategy_version(
    strategy_id: str, payload: StrategyVersionCreate, request: Request
) -> dict[str, Any]:
    db: Database = request.app.state.database
    now = _now()
    with db.connection() as connection:
        strategy = connection.execute(
            "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
        if strategy is None:
            raise HTTPException(status_code=404, detail="Strategy not found")

        latest = connection.execute(
            """
            SELECT * FROM strategy_versions
            WHERE strategy_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
        parameters_json = _canonical_strategy_parameters_json(
            strategy["strategy_type"], payload.fast_window, payload.slow_window
        )
        parameters_sha256 = hashlib.sha256(
            parameters_json.encode("utf-8")
        ).hexdigest()
        if latest is not None and latest["parameters_sha256"] == parameters_sha256:
            raise HTTPException(
                status_code=409,
                detail="Parameters match the current strategy version",
            )

        next_version = int(latest["version"]) + 1 if latest is not None else 1
        version_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO strategy_versions
                (id, strategy_id, version, strategy_type, fast_window,
                 slow_window, parameters_json, parameters_sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                strategy_id,
                next_version,
                strategy["strategy_type"],
                payload.fast_window,
                payload.slow_window,
                parameters_json,
                parameters_sha256,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE strategies
            SET fast_window = ?, slow_window = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.fast_window, payload.slow_window, now, strategy_id),
        )
        version_row = connection.execute(
            "SELECT * FROM strategy_versions WHERE id = ?", (version_id,)
        ).fetchone()
    return _strategy_version_to_dict(version_row)

@router.get("/api/v1/strategies/{strategy_id}")
def get_strategy(strategy_id: str, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        row = _strategy_with_current_version(connection, strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _row_to_dict(row)
