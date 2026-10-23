"""Emergenzix product API. Real-money trading is intentionally unavailable."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, status

from . import __version__
from .backtesting import run_sma_crossover
from .config import Settings
from .database import Database
from .schemas import BacktestCreate, PaperOrderCreate, StrategyCreate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def create_app(
    *,
    db_path: str | Path | None = None,
    initial_paper_cash: float | None = None,
    max_order_notional: float | None = None,
    paper_fee_bps: float | None = None,
) -> FastAPI:
    settings = Settings.from_env()
    starting_cash = (
        initial_paper_cash
        if initial_paper_cash is not None
        else settings.initial_paper_cash
    )
    database = Database(
        str(db_path if db_path is not None else settings.db_path),
        initial_paper_cash=starting_cash,
    )

    app = FastAPI(
        title="Emergenzix API",
        description=(
            "Research-first quantitative trading product API. "
            "This version supports backtesting and paper-only execution."
        ),
        version=__version__,
    )
    app.state.database = database
    app.state.max_order_notional = (
        max_order_notional
        if max_order_notional is not None
        else settings.max_order_notional
    )
    app.state.paper_fee_bps = (
        paper_fee_bps if paper_fee_bps is not None else settings.paper_fee_bps
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "emergenzix-api",
            "version": __version__,
            "mode": "PAPER",
            "live_trading_enabled": False,
        }

    @app.get("/api/v1/overview")
    def overview(request: Request) -> dict[str, Any]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            strategy_count = connection.execute(
                "SELECT COUNT(*) AS count FROM strategies"
            ).fetchone()["count"]
            backtest_count = connection.execute(
                "SELECT COUNT(*) AS count FROM backtests"
            ).fetchone()["count"]
            position_count = connection.execute(
                "SELECT COUNT(*) AS count FROM paper_positions"
            ).fetchone()["count"]
            account = connection.execute(
                "SELECT cash, starting_cash FROM paper_account WHERE id = 1"
            ).fetchone()
            realized = connection.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) AS value "
                "FROM paper_orders WHERE side = 'SELL'"
            ).fetchone()["value"]
        return {
            "mode": "PAPER",
            "live_trading_enabled": False,
            "strategy_count": strategy_count,
            "backtest_count": backtest_count,
            "open_position_count": position_count,
            "paper_cash": round(float(account["cash"]), 8),
            "starting_cash": round(float(account["starting_cash"]), 8),
            "realized_pnl": round(float(realized), 8),
        }

    @app.get("/api/v1/strategies")
    def list_strategies(request: Request) -> list[dict[str, Any]]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM strategies ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    @app.post("/api/v1/strategies", status_code=status.HTTP_201_CREATED)
    def create_strategy(
        payload: StrategyCreate, request: Request
    ) -> dict[str, Any]:
        db: Database = request.app.state.database
        strategy_id = str(uuid.uuid4())
        now = _now()
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
            row = connection.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        return _row_to_dict(row)

    @app.get("/api/v1/strategies/{strategy_id}")
    def get_strategy(strategy_id: str, request: Request) -> dict[str, Any]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return _row_to_dict(row)

    @app.post("/api/v1/backtests", status_code=status.HTTP_201_CREATED)
    def create_backtest(
        payload: BacktestCreate, request: Request
    ) -> dict[str, Any]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            strategy = connection.execute(
                "SELECT * FROM strategies WHERE id = ?", (payload.strategy_id,)
            ).fetchone()
        if strategy is None:
            raise HTTPException(status_code=404, detail="Strategy not found")

        if len(payload.bars) < int(strategy["slow_window"]) + 2:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"At least {int(strategy['slow_window']) + 2} bars are required "
                    "for this strategy"
                ),
            )

        try:
            result = run_sma_crossover(
                payload.bars,
                fast_window=int(strategy["fast_window"]),
                slow_window=int(strategy["slow_window"]),
                initial_cash=payload.initial_cash,
                fee_bps=payload.fee_bps,
                slippage_bps=payload.slippage_bps,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        backtest_id = str(uuid.uuid4())
        created_at = _now()
        result_payload = {
            "id": backtest_id,
            "strategy_id": payload.strategy_id,
            "symbol": strategy["symbol"],
            "strategy": {
                "name": strategy["name"],
                "strategy_type": strategy["strategy_type"],
                "fast_window": int(strategy["fast_window"]),
                "slow_window": int(strategy["slow_window"]),
            },
            "created_at": created_at,
            **result,
        }
        with db.connection() as connection:
            connection.execute(
                """
                INSERT INTO backtests
                    (id, strategy_id, symbol, bars_count, initial_cash,
                     final_equity, total_return_pct, max_drawdown_pct,
                     number_of_trades, win_rate_pct, benchmark_return_pct,
                     result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backtest_id,
                    payload.strategy_id,
                    strategy["symbol"],
                    result["bars_count"],
                    result["initial_cash"],
                    result["final_equity"],
                    result["total_return_pct"],
                    result["max_drawdown_pct"],
                    result["number_of_trades"],
                    result["win_rate_pct"],
                    result["benchmark_return_pct"],
                    json.dumps(result_payload, separators=(",", ":")),
                    created_at,
                ),
            )
        return result_payload

    @app.get("/api/v1/backtests")
    def list_backtests(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            rows = connection.execute(
                "SELECT result_json FROM backtests "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row["result_json"]) for row in rows]

    @app.get("/api/v1/backtests/{backtest_id}")
    def get_backtest(backtest_id: str, request: Request) -> dict[str, Any]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            row = connection.execute(
                "SELECT result_json FROM backtests WHERE id = ?", (backtest_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Backtest not found")
        return json.loads(row["result_json"])

    @app.get("/api/v1/paper/account")
    def paper_account(request: Request) -> dict[str, Any]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            account = connection.execute(
                "SELECT cash, starting_cash, created_at "
                "FROM paper_account WHERE id = 1"
            ).fetchone()
            positions = connection.execute(
                "SELECT symbol, quantity, avg_entry_price FROM paper_positions "
                "ORDER BY symbol"
            ).fetchall()
            realized = connection.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) AS value "
                "FROM paper_orders WHERE side = 'SELL'"
            ).fetchone()["value"]

        position_cost_basis = sum(
            float(position["quantity"]) * float(position["avg_entry_price"])
            for position in positions
        )
        return {
            "mode": "PAPER",
            "live_trading_enabled": False,
            "cash": round(float(account["cash"]), 8),
            "starting_cash": round(float(account["starting_cash"]), 8),
            "position_cost_basis": round(position_cost_basis, 8),
            "equity_at_cost_basis": round(
                float(account["cash"]) + position_cost_basis, 8
            ),
            "realized_pnl": round(float(realized), 8),
            "open_position_count": len(positions),
            "created_at": account["created_at"],
        }

    @app.get("/api/v1/paper/positions")
    def paper_positions(request: Request) -> list[dict[str, Any]]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            rows = connection.execute(
                "SELECT symbol, quantity, avg_entry_price, updated_at "
                "FROM paper_positions ORDER BY symbol"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    @app.get("/api/v1/paper/orders")
    def paper_orders(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    @app.get("/api/v1/paper/trades")
    def paper_trades(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_orders WHERE status = 'FILLED' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    @app.post("/api/v1/paper/orders", status_code=status.HTTP_201_CREATED)
    def create_paper_order(
        payload: PaperOrderCreate, request: Request
    ) -> dict[str, Any]:
        db: Database = request.app.state.database
        notional = float(payload.quantity) * float(payload.reference_price)
        max_notional = float(request.app.state.max_order_notional)
        if notional > max_notional:
            raise HTTPException(
                status_code=422,
                detail=f"Order notional exceeds the paper limit of {max_notional:.2f}",
            )

        if payload.strategy_id:
            with db.connection() as connection:
                strategy = connection.execute(
                    "SELECT id, symbol FROM strategies WHERE id = ?",
                    (payload.strategy_id,),
                ).fetchone()
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if strategy["symbol"] != payload.symbol:
                raise HTTPException(
                    status_code=422,
                    detail="Order symbol must match the selected strategy symbol",
                )

        order_id = str(uuid.uuid4())
        created_at = _now()
        fee_rate = float(request.app.state.paper_fee_bps) / 10_000.0
        fee = notional * fee_rate
        price = float(payload.reference_price)
        quantity = float(payload.quantity)
        realized_pnl = 0.0

        with db.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT cash FROM paper_account WHERE id = 1"
            ).fetchone()
            cash = float(account["cash"])
            position = connection.execute(
                "SELECT quantity, avg_entry_price FROM paper_positions "
                "WHERE symbol = ?",
                (payload.symbol,),
            ).fetchone()

            if payload.side == "BUY":
                required_cash = notional + fee
                if cash + 1e-9 < required_cash:
                    raise HTTPException(
                        status_code=409, detail="Insufficient paper cash"
                    )
                old_quantity = float(position["quantity"]) if position else 0.0
                old_cost = (
                    old_quantity * float(position["avg_entry_price"])
                    if position
                    else 0.0
                )
                new_quantity = old_quantity + quantity
                new_average = (old_cost + notional + fee) / new_quantity
                connection.execute(
                    """
                    INSERT INTO paper_positions
                        (symbol, quantity, avg_entry_price, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        quantity = excluded.quantity,
                        avg_entry_price = excluded.avg_entry_price,
                        updated_at = excluded.updated_at
                    """,
                    (payload.symbol, new_quantity, new_average, created_at),
                )
                cash -= required_cash

            else:
                if position is None or quantity > float(position["quantity"]) + 1e-9:
                    raise HTTPException(
                        status_code=409,
                        detail="Insufficient paper position to sell",
                    )
                old_quantity = float(position["quantity"])
                average_entry = float(position["avg_entry_price"])
                realized_pnl = (notional - fee) - (quantity * average_entry)
                remaining = old_quantity - quantity
                if remaining <= 1e-9:
                    connection.execute(
                        "DELETE FROM paper_positions WHERE symbol = ?",
                        (payload.symbol,),
                    )
                else:
                    connection.execute(
                        "UPDATE paper_positions SET quantity = ?, updated_at = ? "
                        "WHERE symbol = ?",
                        (remaining, created_at, payload.symbol),
                    )
                cash += notional - fee

            connection.execute(
                "UPDATE paper_account SET cash = ? WHERE id = 1", (cash,)
            )
            connection.execute(
                """
                INSERT INTO paper_orders
                    (id, symbol, side, quantity, price, notional, fee,
                     realized_pnl, strategy_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'FILLED', ?)
                """,
                (
                    order_id,
                    payload.symbol,
                    payload.side,
                    quantity,
                    price,
                    notional,
                    fee,
                    realized_pnl,
                    payload.strategy_id,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM paper_orders WHERE id = ?", (order_id,)
            ).fetchone()

        return _row_to_dict(row)

    return app


app = create_app()
