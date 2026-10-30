"""Small SQLite persistence layer for the initial product API."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: str, initial_paper_cash: float = 100_000.0) -> None:
        self.path = str(path)
        self.initial_paper_cash = float(initial_paper_cash)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_type TEXT NOT NULL,
                    fast_window INTEGER NOT NULL,
                    slow_window INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (fast_window >= 2),
                    CHECK (slow_window > fast_window)
                );

                CREATE TABLE IF NOT EXISTS strategy_versions (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    strategy_type TEXT NOT NULL,
                    fast_window INTEGER NOT NULL CHECK (fast_window >= 2),
                    slow_window INTEGER NOT NULL CHECK (slow_window > fast_window),
                    parameters_json TEXT NOT NULL,
                    parameters_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (strategy_id, version)
                );

                CREATE TABLE IF NOT EXISTS backtests (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL REFERENCES strategies(id),
                    strategy_version_id TEXT REFERENCES strategy_versions(id),
                    symbol TEXT NOT NULL,
                    bars_count INTEGER NOT NULL,
                    initial_cash REAL NOT NULL,
                    final_equity REAL NOT NULL,
                    total_return_pct REAL NOT NULL,
                    max_drawdown_pct REAL NOT NULL,
                    number_of_trades INTEGER NOT NULL,
                    win_rate_pct REAL NOT NULL,
                    benchmark_return_pct REAL NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_datasets (
                    id TEXT PRIMARY KEY,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    exchange TEXT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    bars_count INTEGER NOT NULL CHECK (bars_count >= 5),
                    bars_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historical_replay_records (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
                    dataset_sha256 TEXT NOT NULL CHECK (length(dataset_sha256) = 64),
                    symbol TEXT NOT NULL,
                    timeframe TEXT,
                    protocol_version TEXT NOT NULL,
                    seed INTEGER CHECK (seed IS NULL OR seed >= 0),
                    anchor_at TEXT,
                    horizon_bars INTEGER NOT NULL CHECK (horizon_bars >= 1),
                    lookback INTEGER NOT NULL CHECK (lookback >= 10),
                    anchor_index INTEGER NOT NULL CHECK (anchor_index >= 0),
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (dataset_id, seed, anchor_at, horizon_bars, lookback, protocol_version)
                );

                CREATE TABLE IF NOT EXISTS date_range_backtest_records (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
                    dataset_sha256 TEXT NOT NULL CHECK (length(dataset_sha256) = 64),
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 30),
                    training_days INTEGER NOT NULL CHECK (training_days BETWEEN 60 AND 365),
                    protocol_version TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );


                -- Batch plan metadata is immutable. State belongs to child jobs.
                CREATE TABLE IF NOT EXISTS research_batch_plans (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_batch_jobs (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    batch_id TEXT NOT NULL REFERENCES research_batch_plans(id),
                    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 12),
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'leased', 'failed', 'cancelled', 'completed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    last_error_code TEXT,
                    saved_record_id TEXT REFERENCES date_range_backtest_records(id),
                    saved_record_sha256 TEXT CHECK (
                        saved_record_sha256 IS NULL OR length(saved_record_sha256) = 64
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (batch_id, position),
                    CHECK (
                        (state = 'leased' AND lease_token IS NOT NULL
                         AND lease_expires_at IS NOT NULL)
                        OR
                        (state != 'leased' AND lease_token IS NULL
                         AND lease_expires_at IS NULL)
                    ),
                    CHECK (
                        (state = 'completed' AND saved_record_id IS NOT NULL
                         AND saved_record_sha256 IS NOT NULL)
                        OR
                        (state != 'completed' AND saved_record_id IS NULL
                         AND saved_record_sha256 IS NULL)
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_research_batch_jobs_claim
                    ON research_batch_jobs(batch_id, state, position);

                CREATE TRIGGER IF NOT EXISTS research_batch_plans_no_update
                BEFORE UPDATE ON research_batch_plans
                BEGIN
                    SELECT RAISE(ABORT, 'research batch plans are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS research_batch_plans_no_delete
                BEFORE DELETE ON research_batch_plans
                BEGIN
                    SELECT RAISE(ABORT, 'research batch plans are immutable');
                END;

                CREATE TABLE IF NOT EXISTS strategy_comparisons (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL REFERENCES strategies(id),
                    dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
                    strategy_version_ids_json TEXT NOT NULL,
                    initial_cash REAL NOT NULL CHECK (initial_cash > 0),
                    fee_bps REAL NOT NULL CHECK (fee_bps >= 0),
                    slippage_bps REAL NOT NULL CHECK (slippage_bps >= 0),
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_validations (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL REFERENCES strategies(id),
                    strategy_version_id TEXT NOT NULL REFERENCES strategy_versions(id),
                    dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
                    train_fraction REAL NOT NULL CHECK (train_fraction >= 0.5 AND train_fraction <= 0.9),
                    split_index INTEGER NOT NULL CHECK (split_index > 0),
                    initial_cash REAL NOT NULL CHECK (initial_cash > 0),
                    fee_bps REAL NOT NULL CHECK (fee_bps >= 0),
                    slippage_bps REAL NOT NULL CHECK (slippage_bps >= 0),
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    starting_cash REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL CHECK (quantity > 0),
                    avg_entry_price REAL NOT NULL CHECK (avg_entry_price > 0),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_orders (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT,
                    request_sha256 TEXT CHECK (
                        request_sha256 IS NULL OR length(request_sha256) = 64
                    ),
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    quantity REAL NOT NULL CHECK (quantity > 0),
                    price REAL NOT NULL CHECK (price > 0),
                    notional REAL NOT NULL,
                    fee REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    strategy_id TEXT REFERENCES strategies(id) ON DELETE SET NULL,
                    status TEXT NOT NULL CHECK (status = 'FILLED'),
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_versions_strategy_version
                    ON strategy_versions(strategy_id, version DESC);
                CREATE INDEX IF NOT EXISTS idx_market_datasets_created_at
                    ON market_datasets(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_historical_replay_records_created_at
                    ON historical_replay_records(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_historical_replay_records_dataset
                    ON historical_replay_records(dataset_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_date_range_backtest_records_created_at
                    ON date_range_backtest_records(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_date_range_backtest_records_period
                    ON date_range_backtest_records(start_at DESC, end_at DESC);

                CREATE TRIGGER IF NOT EXISTS historical_replay_records_no_update
                BEFORE UPDATE ON historical_replay_records
                BEGIN
                    SELECT RAISE(ABORT, 'historical replay records are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS historical_replay_records_no_delete
                BEFORE DELETE ON historical_replay_records
                BEGIN
                    SELECT RAISE(ABORT, 'historical replay records are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS date_range_backtest_records_no_update
                BEFORE UPDATE ON date_range_backtest_records
                BEGIN
                    SELECT RAISE(ABORT, 'date-range backtest records are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS date_range_backtest_records_no_delete
                BEFORE DELETE ON date_range_backtest_records
                BEGIN
                    SELECT RAISE(ABORT, 'date-range backtest records are immutable');
                END;

                CREATE INDEX IF NOT EXISTS idx_backtests_created_at
                    ON backtests(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_strategy_comparisons_created_at
                    ON strategy_comparisons(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_strategy_validations_created_at
                    ON strategy_validations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_paper_orders_created_at
                    ON paper_orders(created_at DESC);
                """
            )


            # Prior batch schema did not allow completed jobs. SQLite cannot
            # ALTER a CHECK expression, so atomically rebuild that one table.
            # No existing jobs/results are deleted. Indices are recreated.
            batch_schema = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'research_batch_jobs'"
            ).fetchone()["sql"]
            if "'completed'" not in batch_schema:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "ALTER TABLE research_batch_jobs "
                    "RENAME TO research_batch_jobs_legacy_v1"
                )
                connection.execute("DROP INDEX IF EXISTS idx_research_batch_jobs_claim")
                connection.execute(
                    """
                    CREATE TABLE research_batch_jobs (
                        id TEXT PRIMARY KEY CHECK (length(id) = 64),
                        batch_id TEXT NOT NULL REFERENCES research_batch_plans(id),
                        position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 12),
                        request_json TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'pending'
                            CHECK (state IN (
                                'pending', 'leased', 'failed', 'cancelled', 'completed'
                            )),
                        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
                        lease_token TEXT,
                        lease_expires_at TEXT,
                        last_error_code TEXT,
                        saved_record_id TEXT REFERENCES date_range_backtest_records(id),
                        saved_record_sha256 TEXT CHECK (
                            saved_record_sha256 IS NULL OR length(saved_record_sha256) = 64
                        ),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (batch_id, position),
                        CHECK (
                            (state = 'leased' AND lease_token IS NOT NULL
                             AND lease_expires_at IS NOT NULL)
                            OR (state != 'leased' AND lease_token IS NULL
                                AND lease_expires_at IS NULL)
                        ),
                        CHECK (
                            (state = 'completed' AND saved_record_id IS NOT NULL
                             AND saved_record_sha256 IS NOT NULL)
                            OR (state != 'completed' AND saved_record_id IS NULL
                                AND saved_record_sha256 IS NULL)
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO research_batch_jobs (
                        id, batch_id, position, request_json, state, attempts,
                        lease_token, lease_expires_at, last_error_code,
                        created_at, updated_at
                    ) SELECT
                        id, batch_id, position, request_json, state, attempts,
                        lease_token, lease_expires_at, last_error_code,
                        created_at, updated_at
                    FROM research_batch_jobs_legacy_v1
                    """
                )
                connection.execute("DROP TABLE research_batch_jobs_legacy_v1")
                connection.execute(
                    "CREATE INDEX idx_research_batch_jobs_claim "
                    "ON research_batch_jobs(batch_id, state, position)"
                )

            # Existing databases were created before strategy versions existed.
            # Add the nullable provenance column without rewriting historical runs:
            # their exact parameter version cannot be inferred safely.
            backtest_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(backtests)")
            }
            if "strategy_version_id" not in backtest_columns:
                connection.execute(
                    "ALTER TABLE backtests ADD COLUMN strategy_version_id "
                    "TEXT REFERENCES strategy_versions(id)"
                )

            paper_order_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(paper_orders)")
            }
            if "idempotency_key" not in paper_order_columns:
                connection.execute(
                    "ALTER TABLE paper_orders ADD COLUMN idempotency_key TEXT"
                )
            if "request_sha256" not in paper_order_columns:
                connection.execute(
                    "ALTER TABLE paper_orders ADD COLUMN request_sha256 TEXT "
                    "CHECK (request_sha256 IS NULL OR length(request_sha256) = 64)"
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_paper_orders_idempotency_key "
                "ON paper_orders(idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )

            from datetime import datetime, timezone

            # Backfill each legacy strategy with a stable version-1 snapshot.
            # The deterministic ID makes initialization idempotent across restarts.
            legacy_strategies = connection.execute(
                "SELECT * FROM strategies"
            ).fetchall()
            for strategy in legacy_strategies:
                existing_version = connection.execute(
                    "SELECT id FROM strategy_versions WHERE strategy_id = ? LIMIT 1",
                    (strategy["id"],),
                ).fetchone()
                if existing_version is not None:
                    continue

                parameters = {
                    "strategy_type": strategy["strategy_type"],
                    "fast_window": int(strategy["fast_window"]),
                    "slow_window": int(strategy["slow_window"]),
                }
                parameters_json = json.dumps(
                    parameters, sort_keys=True, separators=(",", ":")
                )
                parameters_sha256 = hashlib.sha256(
                    parameters_json.encode("utf-8")
                ).hexdigest()
                version_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"emergenzix:strategy:{strategy['id']}:version:1:{parameters_sha256}",
                    )
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO strategy_versions
                        (id, strategy_id, version, strategy_type, fast_window,
                         slow_window, parameters_json, parameters_sha256, created_at)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        strategy["id"],
                        strategy["strategy_type"],
                        int(strategy["fast_window"]),
                        int(strategy["slow_window"]),
                        parameters_json,
                        parameters_sha256,
                        strategy["created_at"],
                    ),
                )

            connection.execute(
                """
                INSERT OR IGNORE INTO paper_account
                    (id, cash, starting_cash, created_at)
                VALUES (1, ?, ?, ?)
                """,
                (
                    self.initial_paper_cash,
                    self.initial_paper_cash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
