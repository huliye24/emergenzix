"""Fixed, immutable Turtle research matrix with chronological holdouts."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from .api_common import _now
from .backtesting import run_turtle_breakout
from .database import Database
from .dataset_store import DatasetIntegrityError, verify_stored_dataset
from .schemas import Bar, TurtleMatrixCreate

router = APIRouter(tags=["evaluation"])

PROTOCOL_VERSION = "turtle-matrix-chronological-holdout-v1"
SYSTEMS = ((20, 10), (55, 20))


def _symbol_key(value: str) -> str:
    return value.strip().upper().replace("/", "").replace("-", "").replace("_", "")


def _compact(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "bars_count": run["bars_count"],
        "initial_cash": run["initial_cash"],
        "final_equity": run["final_equity"],
        "total_return_pct": run["total_return_pct"],
        "max_drawdown_pct": run["max_drawdown_pct"],
        "number_of_trades": run["number_of_trades"],
        "win_rate_pct": run["win_rate_pct"],
        "benchmark_return_pct": run["benchmark_return_pct"],
    }


@router.post("/api/v1/turtle-matrices", status_code=status.HTTP_201_CREATED)
def create_turtle_matrix(
    payload: TurtleMatrixCreate, request: Request
) -> dict[str, Any]:
    """Evaluate all 18 fixed Turtle arms and persist one ranked OOS record."""
    db: Database = request.app.state.database
    dataset_ids = [item.dataset_id for item in payload.datasets]
    placeholders = ",".join("?" for _ in dataset_ids)
    with db.connection() as connection:
        dataset_rows = connection.execute(
            f"SELECT * FROM market_datasets WHERE id IN ({placeholders})",
            dataset_ids,
        ).fetchall()
        strategy_rows = connection.execute(
            """
            SELECT s.*, v.id AS strategy_version_id,
                   v.version AS strategy_version,
                   v.parameters_sha256 AS parameters_sha256
            FROM strategies AS s
            JOIN strategy_versions AS v ON v.strategy_id = s.id
            WHERE s.strategy_type = 'turtle_breakout'
              AND v.version = (
                  SELECT MAX(v2.version) FROM strategy_versions AS v2
                  WHERE v2.strategy_id = s.id
              )
            """
        ).fetchall()

    by_id = {row["id"]: row for row in dataset_rows}
    strategies = {
        (row["symbol"], int(row["slow_window"]), int(row["fast_window"])): row
        for row in strategy_rows
    }
    if len(by_id) != 9:
        raise HTTPException(status_code=404, detail="One or more matrix datasets were not found")

    results: list[dict[str, Any]] = []
    dataset_receipts: list[dict[str, Any]] = []
    for reference in sorted(payload.datasets, key=lambda item: (item.symbol, item.timeframe)):
        row = by_id[reference.dataset_id]
        if (
            _symbol_key(row["symbol"]) != reference.symbol
            or row["timeframe"] != reference.timeframe
            or row["exchange"] != "binance"
            or row["source"] == "synthetic_demo"
        ):
            raise HTTPException(
                status_code=422,
                detail=f"Dataset metadata does not match {reference.symbol} {reference.timeframe}",
            )
        try:
            verified = verify_stored_dataset(row)
            bars = [Bar.model_validate(item) for item in verified]
        except (DatasetIntegrityError, ValueError) as exc:
            raise HTTPException(
                status_code=409, detail="Matrix dataset failed integrity validation"
            ) from exc

        split_index = int(len(bars) * payload.train_fraction)
        if split_index < 57 or len(bars) - split_index < 20:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{reference.symbol} {reference.timeframe} needs at least 57 "
                    "in-sample bars and 20 out-of-sample bars"
                ),
            )
        dataset_receipts.append(
            {
                "dataset_id": row["id"],
                "dataset_sha256": row["content_sha256"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "start_at": row["start_at"],
                "end_at": row["end_at"],
                "bars_count": int(row["bars_count"]),
                "split_index": split_index,
                "oos_start_at": bars[split_index].timestamp.isoformat(),
            }
        )

        for entry_window, exit_window in SYSTEMS:
            strategy = strategies.get((reference.symbol, entry_window, exit_window))
            if strategy is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Built-in Turtle {entry_window}/{exit_window} for {reference.symbol} is unavailable",
                )
            try:
                in_sample = run_turtle_breakout(
                    bars[:split_index],
                    entry_window=entry_window,
                    exit_window=exit_window,
                    initial_cash=payload.initial_cash,
                    fee_bps=payload.fee_bps,
                    slippage_bps=payload.slippage_bps,
                )
                out_of_sample = run_turtle_breakout(
                    bars,
                    entry_window=entry_window,
                    exit_window=exit_window,
                    initial_cash=payload.initial_cash,
                    fee_bps=payload.fee_bps,
                    slippage_bps=payload.slippage_bps,
                    evaluation_start_index=split_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            results.append(
                {
                    "symbol": reference.symbol,
                    "timeframe": reference.timeframe,
                    "system": f"{entry_window}/{exit_window}",
                    "strategy_id": strategy["id"],
                    "strategy_version_id": strategy["strategy_version_id"],
                    "strategy_sha256": strategy["parameters_sha256"],
                    "dataset_id": row["id"],
                    "dataset_sha256": row["content_sha256"],
                    "in_sample": _compact(in_sample),
                    "out_of_sample": _compact(out_of_sample),
                    "oos_excess_return_pct": round(
                        out_of_sample["total_return_pct"]
                        - out_of_sample["benchmark_return_pct"],
                        6,
                    ),
                }
            )

    ranked = sorted(
        results,
        key=lambda item: (
            -float(item["oos_excess_return_pct"]),
            -float(item["out_of_sample"]["total_return_pct"]),
            float(item["out_of_sample"]["max_drawdown_pct"]),
            item["symbol"],
            item["timeframe"],
            item["system"],
        ),
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank

    canonical_input = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "datasets": dataset_receipts,
            "train_fraction": payload.train_fraction,
            "initial_cash": payload.initial_cash,
            "fee_bps": payload.fee_bps,
            "slippage_bps": payload.slippage_bps,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    matrix_id = str(uuid.uuid4())
    created_at = _now()
    result = {
        "id": matrix_id,
        "created_at": created_at,
        "protocol_version": PROTOCOL_VERSION,
        "input_sha256": hashlib.sha256(canonical_input.encode("utf-8")).hexdigest(),
        "matrix_shape": {"assets": 3, "timeframes": 3, "systems": 2, "arms": 18},
        "split": {
            "method": "chronological_fixed_holdout",
            "train_fraction": payload.train_fraction,
            "warmup_policy": "Pre-split bars initialize indicators only; OOS starts flat at the split boundary.",
        },
        "configuration": {
            "initial_cash": payload.initial_cash,
            "fee_bps": payload.fee_bps,
            "slippage_bps": payload.slippage_bps,
        },
        "datasets": dataset_receipts,
        "ranking_method": "OOS excess return descending, then OOS return descending, then drawdown ascending",
        "ranking": ranked,
        "safety": {
            "execution_mode": "BACKTEST_ONLY",
            "paper_order_permission": False,
            "live_order_permission": False,
            "automatic_strategy_promotion": False,
        },
    }
    with db.connection() as connection:
        connection.execute(
            """
            INSERT INTO turtle_matrix_runs
                (id, protocol_version, result_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                matrix_id,
                PROTOCOL_VERSION,
                json.dumps(result, separators=(",", ":"), allow_nan=False),
                created_at,
            ),
        )
    return result


@router.get("/api/v1/turtle-matrices")
def list_turtle_matrices(
    request: Request, limit: int = Query(default=10, ge=1, le=50)
) -> list[dict[str, Any]]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT result_json FROM turtle_matrix_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [json.loads(row["result_json"]) for row in rows]


@router.get("/api/v1/turtle-matrices/{matrix_id}")
def get_turtle_matrix(matrix_id: str, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.database
    with db.connection() as connection:
        row = connection.execute(
            "SELECT result_json FROM turtle_matrix_runs WHERE id = ?", (matrix_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Turtle matrix not found")
    return json.loads(row["result_json"])
