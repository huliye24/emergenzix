"""Emergenzix product API. Real-money trading is intentionally unavailable."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response, status

from . import __version__
from .backtesting import run_sma_crossover
from .config import Settings
from .dataset_quality import build_dataset_quality_report
from .database import Database
from .market_data import MarketDataUnavailable, fetch_public_ohlcv, normalize_symbol
from .market_data_cache import TTLCache
from .schemas import Bar, BacktestCreate, DatasetCreate, PaperOrderCreate, StrategyCreate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


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


def _dataset_to_dict(row: Any, *, include_bars: bool = False) -> dict[str, Any]:
    """Serialize dataset metadata, optionally including the immutable OHLCV payload."""
    result: dict[str, Any] = {
        "id": row["id"],
        "sha256": row["content_sha256"],
        "source": row["source"],
        "exchange": row["exchange"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "bars_count": int(row["bars_count"]),
        "created_at": row["created_at"],
    }
    if include_bars:
        result["bars"] = json.loads(row["bars_json"])
    return result


def _symbol_key(symbol: str) -> str:
    """Normalize common separator differences for strategy/dataset compatibility."""
    return symbol.strip().upper().replace("/", "").replace("-", "").replace("_", "")


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
    app.state.market_data_cache = TTLCache(
        ttl_seconds=settings.market_data_cache_ttl_seconds,
        max_entries=settings.market_data_cache_max_entries,
    )
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

    @app.get("/api/v1/market-data/ohlcv")
    def market_data_ohlcv(
        exchange: Literal["binance", "bybit", "okx", "kraken", "coinbase"] = Query(
            default="binance"
        ),
        symbol: str = Query(default="BTCUSDT", min_length=3, max_length=30),
        timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"] = Query(
            default="1h"
        ),
        limit: int = Query(default=200, ge=5, le=1000),
    ) -> dict[str, Any]:
        """Fetch public exchange OHLCV candles. This route cannot place orders."""
        try:
            normalized_symbol = normalize_symbol(symbol)
            cache_key = (exchange, normalized_symbol, timeframe, limit)
            cache: TTLCache = request.app.state.market_data_cache
            return cache.get_or_create(
                cache_key,
                lambda: fetch_public_ohlcv(
                    exchange_id=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/v1/datasets", status_code=status.HTTP_201_CREATED)
    def register_dataset(
        payload: DatasetCreate, request: Request, response: Response
    ) -> dict[str, Any]:
        """Persist validated OHLCV bars under a deterministic SHA-256 dataset ID."""
        db: Database = request.app.state.database
        dataset_symbol = payload.symbol
        if payload.source == "ccxt_public_ohlcv":
            try:
                dataset_symbol = normalize_symbol(payload.symbol)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        canonical_bars = [
            {
                "timestamp": bar.timestamp.astimezone(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }
            for bar in payload.bars
        ]
        canonical_payload = {
            "source": payload.source,
            "exchange": payload.exchange,
            "symbol": dataset_symbol,
            "timeframe": payload.timeframe,
            "bars": canonical_bars,
        }
        canonical_json = json.dumps(
            canonical_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        dataset_id = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        bars_json = json.dumps(
            canonical_bars,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        created_at = _now()

        with db.connection() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO market_datasets
                    (id, content_sha256, source, exchange, symbol, timeframe,
                     start_at, end_at, bars_count, bars_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    dataset_id,
                    payload.source,
                    payload.exchange,
                    dataset_symbol,
                    payload.timeframe,
                    canonical_bars[0]["timestamp"],
                    canonical_bars[-1]["timestamp"],
                    len(canonical_bars),
                    bars_json,
                    created_at,
                ),
            )
            if inserted.rowcount == 0:
                response.status_code = status.HTTP_200_OK
            row = connection.execute(
                "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        return _dataset_to_dict(row)

    @app.get("/api/v1/datasets")
    def list_datasets(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        """List dataset metadata without returning potentially large candle arrays."""
        db: Database = request.app.state.database
        with db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM market_datasets ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_dataset_to_dict(row) for row in rows]

    @app.get("/api/v1/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
        """Retrieve the exact OHLCV bars associated with an immutable dataset ID."""
        db: Database = request.app.state.database
        with db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return _dataset_to_dict(row, include_bars=True)

    @app.get("/api/v1/datasets/{dataset_id}/quality")
    def get_dataset_quality(dataset_id: str, request: Request) -> dict[str, Any]:
        """Return read-only interval-gap, coverage, and freshness diagnostics."""
        db: Database = request.app.state.database
        with db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return build_dataset_quality_report(
            dataset_id=row["id"],
            sha256=row["content_sha256"],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            bars_json=row["bars_json"],
        )

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

    @app.post("/api/v1/strategies", status_code=status.HTTP_201_CREATED)
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

    @app.get("/api/v1/strategies/{strategy_id}/versions")
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

    @app.post(
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

    @app.get("/api/v1/strategies/{strategy_id}")
    def get_strategy(strategy_id: str, request: Request) -> dict[str, Any]:
        db: Database = request.app.state.database
        with db.connection() as connection:
            row = _strategy_with_current_version(connection, strategy_id)
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
            strategy_version = None
            if strategy is not None:
                if payload.strategy_version_id is not None:
                    strategy_version = connection.execute(
                        "SELECT * FROM strategy_versions WHERE id = ?",
                        (payload.strategy_version_id,),
                    ).fetchone()
                else:
                    strategy_version = connection.execute(
                        """
                        SELECT * FROM strategy_versions
                        WHERE strategy_id = ?
                        ORDER BY version DESC
                        LIMIT 1
                        """,
                        (payload.strategy_id,),
                    ).fetchone()
            dataset = (
                connection.execute(
                    "SELECT * FROM market_datasets WHERE id = ?", (payload.dataset_id,)
                ).fetchone()
                if payload.dataset_id is not None
                else None
            )
        if strategy is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        if strategy_version is None:
            raise HTTPException(status_code=404, detail="Strategy version not found")
        if strategy_version["strategy_id"] != strategy["id"]:
            raise HTTPException(
                status_code=422,
                detail="Strategy version does not belong to the selected strategy",
            )

        fast_window = int(strategy_version["fast_window"])
        slow_window = int(strategy_version["slow_window"])
        data_provenance: dict[str, Any]
        if payload.dataset_id is not None:
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            if _symbol_key(dataset["symbol"]) != _symbol_key(strategy["symbol"]):
                raise HTTPException(
                    status_code=422,
                    detail="Dataset symbol does not match strategy symbol",
                )
            bars = [
                Bar.model_validate(item)
                for item in json.loads(dataset["bars_json"])
            ]
            data_provenance = {
                "source": dataset["source"],
                "exchange": dataset["exchange"],
                "symbol": dataset["symbol"],
                "timeframe": dataset["timeframe"],
                "dataset_id": dataset["id"],
                "dataset_sha256": dataset["content_sha256"],
                "start_at": dataset["start_at"],
                "end_at": dataset["end_at"],
                "bars_count": int(dataset["bars_count"]),
            }
        else:
            if payload.bars is None:
                raise HTTPException(
                    status_code=422,
                    detail="bars are required when dataset_id is not provided",
                )
            bars = payload.bars
            data_provenance = {
                "source": payload.data_source,
                "exchange": payload.data_exchange,
                "symbol": payload.data_symbol,
                "timeframe": payload.data_timeframe,
            }

        if len(bars) < slow_window + 2:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"At least {slow_window + 2} bars are required "
                    "for this strategy version"
                ),
            )

        try:
            result = run_sma_crossover(
                bars,
                fast_window=fast_window,
                slow_window=slow_window,
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
            "strategy_version_id": strategy_version["id"],
            "symbol": strategy["symbol"],
            "strategy": {
                "name": strategy["name"],
                "strategy_type": strategy_version["strategy_type"],
                "version": int(strategy_version["version"]),
                "version_id": strategy_version["id"],
                "parameters_sha256": strategy_version["parameters_sha256"],
                "parameters": json.loads(strategy_version["parameters_json"]),
                "fast_window": fast_window,
                "slow_window": slow_window,
            },
            "created_at": created_at,
            "data_provenance": data_provenance,
            **result,
        }
        with db.connection() as connection:
            connection.execute(
                """
                INSERT INTO backtests
                    (id, strategy_id, strategy_version_id, symbol, bars_count,
                     initial_cash, final_equity, total_return_pct,
                     max_drawdown_pct, number_of_trades, win_rate_pct,
                     benchmark_return_pct, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backtest_id,
                    payload.strategy_id,
                    strategy_version["id"],
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
