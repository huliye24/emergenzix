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
                CREATE INDEX IF NOT EXISTS idx_backtests_created_at
                    ON backtests(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_paper_orders_created_at
                    ON paper_orders(created_at DESC);
                """
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
