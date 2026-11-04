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
    def __init__(
        self,
        path: str,
        initial_paper_cash: float = 100_000.0,
        seed_builtin_strategies: bool = False,
    ) -> None:
        self.path = str(path)
        self.initial_paper_cash = float(initial_paper_cash)
        self.seed_builtin_strategies = bool(seed_builtin_strategies)
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

                CREATE TABLE IF NOT EXISTS shadow_sessions (
                    id TEXT PRIMARY KEY,
                    source_backtest_id TEXT NOT NULL REFERENCES backtests(id),
                    strategy_id TEXT NOT NULL REFERENCES strategies(id),
                    strategy_version_id TEXT NOT NULL REFERENCES strategy_versions(id),
                    strategy_sha256 TEXT NOT NULL CHECK (length(strategy_sha256) = 64),
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    initial_cash REAL NOT NULL CHECK (initial_cash > 0),
                    fee_bps REAL NOT NULL CHECK (fee_bps >= 0),
                    slippage_bps REAL NOT NULL CHECK (slippage_bps >= 0),
                    cash REAL NOT NULL CHECK (cash >= 0),
                    quantity REAL NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                    average_entry_price REAL NOT NULL DEFAULT 0 CHECK (average_entry_price >= 0),
                    units INTEGER NOT NULL DEFAULT 0 CHECK (units BETWEEN 0 AND 4),
                    last_add_price REAL NOT NULL DEFAULT 0 CHECK (last_add_price >= 0),
                    pending_action TEXT CHECK (pending_action IN ('ENTER', 'ADD', 'EXIT')),
                    pending_signal_at TEXT,
                    pending_atr REAL CHECK (pending_atr IS NULL OR pending_atr > 0),
                    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED')),
                    last_completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shadow_decisions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES shadow_sessions(id) ON DELETE CASCADE,
                    completed_at TEXT NOT NULL,
                    signal TEXT NOT NULL CHECK (signal IN ('HOLD', 'ENTER', 'ADD', 'EXIT')),
                    reason TEXT NOT NULL,
                    fill_side TEXT CHECK (fill_side IN ('BUY', 'SELL')),
                    fill_quantity REAL NOT NULL DEFAULT 0 CHECK (fill_quantity >= 0),
                    fill_price REAL CHECK (fill_price IS NULL OR fill_price > 0),
                    fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
                    close_price REAL NOT NULL CHECK (close_price > 0),
                    cash REAL NOT NULL CHECK (cash >= 0),
                    position_quantity REAL NOT NULL CHECK (position_quantity >= 0),
                    average_entry_price REAL NOT NULL CHECK (average_entry_price >= 0),
                    equity REAL NOT NULL CHECK (equity >= 0),
                    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, completed_at)
                );

                CREATE INDEX IF NOT EXISTS idx_shadow_decisions_session_time
                    ON shadow_decisions(session_id, completed_at DESC);

                CREATE TRIGGER IF NOT EXISTS shadow_decisions_no_update
                BEFORE UPDATE ON shadow_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'shadow decisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS shadow_decisions_no_delete
                BEFORE DELETE ON shadow_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'shadow decisions are immutable');
                END;

                CREATE TABLE IF NOT EXISTS shadow_cycle_failures (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES shadow_sessions(id) ON DELETE CASCADE,
                    expected_completed_at TEXT NOT NULL,
                    error_code TEXT NOT NULL CHECK (error_code IN (
                        'MARKET_DATA_UNAVAILABLE',
                        'INVALID_MARKET_DATA',
                        'SHADOW_RUNNER_ERROR',
                        'UNEXPECTED_WORKER_ERROR'
                    )),
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, expected_completed_at)
                );

                CREATE INDEX IF NOT EXISTS idx_shadow_cycle_failures_session_time
                    ON shadow_cycle_failures(session_id, expected_completed_at DESC);

                CREATE TRIGGER IF NOT EXISTS shadow_cycle_failures_no_update
                BEFORE UPDATE ON shadow_cycle_failures
                BEGIN
                    SELECT RAISE(ABORT, 'shadow cycle failures are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS shadow_cycle_failures_no_delete
                BEFORE DELETE ON shadow_cycle_failures
                BEGIN
                    SELECT RAISE(ABORT, 'shadow cycle failures are immutable');
                END;

                CREATE TABLE IF NOT EXISTS short_shadow_sessions (
                    id TEXT PRIMARY KEY,
                    fleet_id TEXT NOT NULL,
                    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
                        'donchian_breakdown_20_10',
                        'ema_bear_trend_20_50',
                        'bollinger_breakdown_20_2'
                    )),
                    strategy_version TEXT NOT NULL,
                    strategy_sha256 TEXT NOT NULL CHECK (length(strategy_sha256) = 64),
                    parameters_json TEXT NOT NULL,
                    symbol TEXT NOT NULL CHECK (symbol = 'BTCUSDT'),
                    timeframe TEXT NOT NULL,
                    initial_cash REAL NOT NULL CHECK (initial_cash > 0),
                    fee_bps REAL NOT NULL CHECK (fee_bps >= 0),
                    slippage_bps REAL NOT NULL CHECK (slippage_bps >= 0),
                    cash REAL NOT NULL CHECK (cash >= 0),
                    short_quantity REAL NOT NULL DEFAULT 0 CHECK (short_quantity >= 0),
                    average_entry_price REAL NOT NULL DEFAULT 0 CHECK (average_entry_price >= 0),
                    stop_price REAL NOT NULL DEFAULT 0 CHECK (stop_price >= 0),
                    pending_action TEXT CHECK (pending_action IN ('SHORT', 'COVER')),
                    pending_signal_at TEXT,
                    pending_atr REAL CHECK (pending_atr IS NULL OR pending_atr > 0),
                    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED')),
                    last_completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_short_shadow_sessions_fleet
                    ON short_shadow_sessions(fleet_id, strategy_key);

                CREATE TABLE IF NOT EXISTS short_shadow_decisions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES short_shadow_sessions(id) ON DELETE CASCADE,
                    completed_at TEXT NOT NULL,
                    signal TEXT NOT NULL CHECK (signal IN ('HOLD', 'SHORT', 'COVER')),
                    reason TEXT NOT NULL,
                    fill_side TEXT CHECK (fill_side IN ('SHORT', 'COVER')),
                    fill_quantity REAL NOT NULL DEFAULT 0 CHECK (fill_quantity >= 0),
                    fill_price REAL CHECK (fill_price IS NULL OR fill_price > 0),
                    fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
                    close_price REAL NOT NULL CHECK (close_price > 0),
                    cash REAL NOT NULL CHECK (cash >= 0),
                    short_quantity REAL NOT NULL CHECK (short_quantity >= 0),
                    average_entry_price REAL NOT NULL CHECK (average_entry_price >= 0),
                    equity REAL NOT NULL CHECK (equity >= 0),
                    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, completed_at)
                );

                CREATE INDEX IF NOT EXISTS idx_short_shadow_decisions_session_time
                    ON short_shadow_decisions(session_id, completed_at DESC);

                CREATE TRIGGER IF NOT EXISTS short_shadow_decisions_no_update
                BEFORE UPDATE ON short_shadow_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'short shadow decisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS short_shadow_decisions_no_delete
                BEFORE DELETE ON short_shadow_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'short shadow decisions are immutable');
                END;

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

                CREATE TABLE IF NOT EXISTS turtle_matrix_runs (
                    id TEXT PRIMARY KEY,
                    protocol_version TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS turtle_matrix_runs_no_update
                BEFORE UPDATE ON turtle_matrix_runs
                BEGIN
                    SELECT RAISE(ABORT, 'turtle matrix runs are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS turtle_matrix_runs_no_delete
                BEFORE DELETE ON turtle_matrix_runs
                BEGIN
                    SELECT RAISE(ABORT, 'turtle matrix runs are immutable');
                END;

                CREATE TABLE IF NOT EXISTS eami_model_versions (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    training_parameters_json TEXT NOT NULL,
                    code_sha256 TEXT NOT NULL CHECK (length(code_sha256) = 64),
                    artifact_sha256 TEXT CHECK (
                        artifact_sha256 IS NULL OR length(artifact_sha256) = 64
                    ),
                    parent_model_id TEXT REFERENCES eami_model_versions(id),
                    created_by TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL,
                    UNIQUE (model_name, model_version)
                );

                CREATE TABLE IF NOT EXISTS eami_experiment_records (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    model_id TEXT NOT NULL REFERENCES eami_model_versions(id),
                    dataset_id TEXT REFERENCES market_datasets(id),
                    dataset_sha256 TEXT CHECK (
                        dataset_sha256 IS NULL OR length(dataset_sha256) = 64
                    ),
                    input_asset_id TEXT REFERENCES eami_data_assets(id),
                    output_asset_id TEXT REFERENCES eami_data_assets(id),
                    data_start_at TEXT NOT NULL,
                    data_end_at TEXT NOT NULL,
                    protocol_version TEXT NOT NULL,
                    split_definition_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (
                        outcome IN ('PASSED', 'FAILED', 'INCONCLUSIVE')
                    ),
                    random_seed INTEGER CHECK (random_seed IS NULL OR random_seed >= 0),
                    notes TEXT,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL,
                    CHECK ((dataset_id IS NOT NULL) != (input_asset_id IS NOT NULL))
                );

                CREATE INDEX IF NOT EXISTS idx_eami_experiments_model_time
                    ON eami_experiment_records(model_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS eami_model_stage_events (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    model_id TEXT NOT NULL REFERENCES eami_model_versions(id),
                    experiment_id TEXT REFERENCES eami_experiment_records(id),
                    from_stage TEXT,
                    to_stage TEXT NOT NULL CHECK (
                        to_stage IN ('REGISTERED', 'CHALLENGER', 'CHAMPION', 'REJECTED', 'RETIRED')
                    ),
                    rationale TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_eami_stage_events_model_time
                    ON eami_model_stage_events(model_id, created_at, id);

                CREATE TRIGGER IF NOT EXISTS eami_model_versions_no_update
                BEFORE UPDATE ON eami_model_versions
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI model versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS eami_model_versions_no_delete
                BEFORE DELETE ON eami_model_versions
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI model versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS eami_experiment_records_no_update
                BEFORE UPDATE ON eami_experiment_records
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI experiment records are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS eami_experiment_records_no_delete
                BEFORE DELETE ON eami_experiment_records
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI experiment records are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS eami_model_stage_events_no_update
                BEFORE UPDATE ON eami_model_stage_events
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI model stage events are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS eami_model_stage_events_no_delete
                BEFORE DELETE ON eami_model_stage_events
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI model stage events are immutable');
                END;

                CREATE TABLE IF NOT EXISTS eami_data_assets (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    layer TEXT NOT NULL CHECK (
                        layer IN ('RAW', 'CANONICAL', 'FEATURE', 'RESEARCH_MEMORY')
                    ),
                    asset_kind TEXT NOT NULL,
                    storage_uri TEXT NOT NULL,
                    storage_format TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    feature_version TEXT,
                    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
                    producer_code_sha256 TEXT CHECK (
                        producer_code_sha256 IS NULL OR length(producer_code_sha256) = 64
                    ),
                    source_asset_ids_json TEXT NOT NULL,
                    symbol TEXT,
                    timeframe TEXT,
                    data_start_at TEXT,
                    data_end_at TEXT,
                    metadata_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL,
                    UNIQUE (storage_uri, content_sha256)
                );

                CREATE INDEX IF NOT EXISTS idx_eami_data_assets_layer_time
                    ON eami_data_assets(layer, created_at DESC);

                CREATE TRIGGER IF NOT EXISTS eami_data_assets_no_update
                BEFORE UPDATE ON eami_data_assets
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI data assets are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS eami_data_assets_no_delete
                BEFORE DELETE ON eami_data_assets
                BEGIN
                    SELECT RAISE(ABORT, 'EAMI data assets are immutable');
                END;

                CREATE TABLE IF NOT EXISTS eami_inference_sessions (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    model_id TEXT NOT NULL REFERENCES eami_model_versions(id),
                    input_asset_id TEXT NOT NULL REFERENCES eami_data_assets(id),
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL CHECK (timeframe IN ('15m', '1h', '4h', '1d')),
                    horizon_bars INTEGER NOT NULL CHECK (horizon_bars BETWEEN 1 AND 24),
                    first_expected_at TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eami_inference_observations (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    session_id TEXT NOT NULL REFERENCES eami_inference_sessions(id),
                    completed_at TEXT NOT NULL,
                    target_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('PREDICTION', 'FAILURE')),
                    input_sha256 TEXT,
                    direction TEXT CHECK (direction IN ('UP', 'DOWN', 'NEUTRAL')),
                    score REAL CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
                    prediction_json TEXT,
                    error_code TEXT,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, completed_at),
                    CHECK (
                        (status = 'PREDICTION' AND input_sha256 IS NOT NULL
                         AND direction IS NOT NULL AND score IS NOT NULL
                         AND prediction_json IS NOT NULL AND error_code IS NULL)
                        OR
                        (status = 'FAILURE' AND input_sha256 IS NULL
                         AND direction IS NULL AND score IS NULL
                         AND prediction_json IS NULL AND error_code IS NOT NULL)
                    )
                );

                CREATE TABLE IF NOT EXISTS eami_inference_outcomes (
                    id TEXT PRIMARY KEY CHECK (length(id) = 64),
                    observation_id TEXT NOT NULL UNIQUE
                        REFERENCES eami_inference_observations(id),
                    label TEXT NOT NULL CHECK (label IN ('UP', 'DOWN', 'NEUTRAL')),
                    actual_return REAL NOT NULL,
                    outcome_json TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_eami_inference_observations_session_time
                    ON eami_inference_observations(session_id, completed_at);

                CREATE TRIGGER IF NOT EXISTS eami_inference_sessions_no_update
                BEFORE UPDATE ON eami_inference_sessions BEGIN
                    SELECT RAISE(ABORT, 'EAMI inference sessions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS eami_inference_sessions_no_delete
                BEFORE DELETE ON eami_inference_sessions BEGIN
                    SELECT RAISE(ABORT, 'EAMI inference sessions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS eami_inference_observations_no_update
                BEFORE UPDATE ON eami_inference_observations BEGIN
                    SELECT RAISE(ABORT, 'EAMI inference observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS eami_inference_observations_no_delete
                BEFORE DELETE ON eami_inference_observations BEGIN
                    SELECT RAISE(ABORT, 'EAMI inference observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS eami_inference_outcomes_no_update
                BEFORE UPDATE ON eami_inference_outcomes BEGIN
                    SELECT RAISE(ABORT, 'EAMI inference outcomes are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS eami_inference_outcomes_no_delete
                BEFORE DELETE ON eami_inference_outcomes BEGIN
                    SELECT RAISE(ABORT, 'EAMI inference outcomes are immutable');
                END;

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

                -- Durable order-operation state is separate from the legacy
                -- immediate-fill paper_orders table until the new lifecycle
                -- is integrated behind an explicit API migration.
                CREATE TABLE IF NOT EXISTS paper_order_snapshots (
                    order_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'CREATED', 'SUBMITTED', 'ACKNOWLEDGED',
                        'PARTIALLY_FILLED', 'CANCEL_PENDING', 'FILLED',
                        'CANCELED', 'REJECTED', 'EXPIRED', 'UNKNOWN'
                    )),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_order_events (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL
                        REFERENCES paper_order_snapshots(order_id),
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    resulting_state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_operational_control (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    new_orders_paused INTEGER NOT NULL DEFAULT 1
                        CHECK (new_orders_paused IN (0, 1)),
                    reason TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_v2_order_intents (
                    order_id TEXT PRIMARY KEY
                        REFERENCES paper_order_snapshots(order_id),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    requested_quantity TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_v2_positions (
                    symbol TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_ledger_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES paper_v2_order_intents(order_id),
                    fill_event_id TEXT NOT NULL UNIQUE,
                    realized_pnl TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL
                        REFERENCES paper_ledger_transactions(transaction_id),
                    account TEXT NOT NULL,
                    debit TEXT NOT NULL,
                    credit TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS paper_ledger_transactions_no_update
                BEFORE UPDATE ON paper_ledger_transactions BEGIN
                    SELECT RAISE(ABORT, 'paper ledger transactions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS paper_ledger_transactions_no_delete
                BEFORE DELETE ON paper_ledger_transactions BEGIN
                    SELECT RAISE(ABORT, 'paper ledger transactions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS paper_ledger_entries_no_update
                BEFORE UPDATE ON paper_ledger_entries BEGIN
                    SELECT RAISE(ABORT, 'paper ledger entries are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS paper_ledger_entries_no_delete
                BEFORE DELETE ON paper_ledger_entries BEGIN
                    SELECT RAISE(ABORT, 'paper ledger entries are immutable');
                END;

                CREATE INDEX IF NOT EXISTS idx_paper_order_events_order
                    ON paper_order_events(order_id, created_at, event_id);

                CREATE TRIGGER IF NOT EXISTS paper_order_events_no_update
                BEFORE UPDATE ON paper_order_events
                BEGIN
                    SELECT RAISE(ABORT, 'paper order events are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS paper_order_events_no_delete
                BEFORE DELETE ON paper_order_events
                BEGIN
                    SELECT RAISE(ABORT, 'paper order events are immutable');
                END;

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
                CREATE INDEX IF NOT EXISTS idx_turtle_matrix_runs_created_at
                    ON turtle_matrix_runs(created_at DESC);
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

            # Built-in, immutable Turtle baselines are available to the
            # viewer UI without granting visitor sessions mutation rights.
            builtin_symbols = (
                ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
                if self.seed_builtin_strategies
                else ()
            )
            for symbol in builtin_symbols:
                for entry_window, exit_window in ((20, 10), (55, 20)):
                    system = f"{entry_window}-{exit_window}"
                    strategy_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"emergenzix:builtin:turtle-{system}:{symbol}",
                        )
                    )
                    version_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"emergenzix:builtin:turtle-{system}:{symbol}:v1",
                        )
                    )
                    parameters = {
                        "strategy_type": "turtle_breakout",
                        "fast_window": exit_window,
                        "slow_window": entry_window,
                    }
                    parameters_json = json.dumps(
                        parameters, sort_keys=True, separators=(",", ":")
                    )
                    parameters_sha256 = hashlib.sha256(
                        parameters_json.encode("utf-8")
                    ).hexdigest()
                    created_at = "2026-11-03T00:00:00+00:00"
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO strategies
                            (id, name, symbol, strategy_type, fast_window, slow_window,
                             status, created_at, updated_at)
                        VALUES (?, ?, ?, 'turtle_breakout', ?, ?,
                                'candidate', ?, ?)
                        """,
                        (
                            strategy_id,
                            f"{symbol.removesuffix('USDT')} Turtle {entry_window}/{exit_window}",
                            symbol,
                            exit_window,
                            entry_window,
                            created_at,
                            created_at,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO strategy_versions
                            (id, strategy_id, version, strategy_type, fast_window,
                             slow_window, parameters_json, parameters_sha256, created_at)
                        VALUES (?, ?, 1, 'turtle_breakout', ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id,
                            strategy_id,
                            exit_window,
                            entry_window,
                            parameters_json,
                            parameters_sha256,
                            created_at,
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
            connection.execute(
                """
                INSERT OR IGNORE INTO paper_operational_control
                    (id, new_orders_paused, reason, revision, updated_at)
                VALUES (1, 1, 'paper_v2_not_activated', 0, ?)
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )
