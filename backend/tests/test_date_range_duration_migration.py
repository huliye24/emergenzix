"""Fresh and legacy SQLite acceptance for long date-range records."""
import sqlite3

import pytest

from emergenzix_api.database import DATE_RANGE_DURATION_MIGRATION, Database


DATASET_ID = "d" * 64
RECORD_ID = "r" * 64
PLAN_ID = "p" * 64
JOB_ID = "j" * 64


def _insert_record(connection, record_id, days):
    connection.execute(
        """
        INSERT INTO date_range_backtest_records (
            id, dataset_id, dataset_sha256, start_at, end_at,
            duration_days, training_days, protocol_version,
            record_sha256, record_json, created_at
        ) VALUES (?, ?, ?, '2025-01-01', '2025-01-31', ?, 90,
                  'test-v1', ?, '{}', '2025-02-01')
        """,
        (record_id, DATASET_ID, DATASET_ID, days, record_id),
    )


def _legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE market_datasets (
            id TEXT PRIMARY KEY, content_sha256 TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL, exchange TEXT, symbol TEXT NOT NULL,
            timeframe TEXT, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
            bars_count INTEGER NOT NULL CHECK (bars_count >= 5),
            bars_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE date_range_backtest_records (
            id TEXT PRIMARY KEY CHECK (length(id) = 64),
            dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
            dataset_sha256 TEXT NOT NULL CHECK (length(dataset_sha256) = 64),
            start_at TEXT NOT NULL, end_at TEXT NOT NULL,
            duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 30),
            training_days INTEGER NOT NULL CHECK (training_days BETWEEN 60 AND 365),
            protocol_version TEXT NOT NULL,
            record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
            record_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE research_batch_plans (
            id TEXT PRIMARY KEY CHECK (length(id) = 64),
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE research_batch_jobs (
            id TEXT PRIMARY KEY CHECK (length(id) = 64),
            batch_id TEXT NOT NULL REFERENCES research_batch_plans(id),
            position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 12),
            request_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('pending', 'completed')),
            attempts INTEGER NOT NULL CHECK (attempts BETWEEN 0 AND 3),
            lease_token TEXT, lease_expires_at TEXT, last_error_code TEXT,
            saved_record_id TEXT REFERENCES date_range_backtest_records(id),
            saved_record_sha256 TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO market_datasets VALUES (?, ?, 'test', 'binance', "
        "'BTC/USDT', '4h', '2024-01-01', '2025-01-01', 5, '[]', '2025-01-01')",
        (DATASET_ID, DATASET_ID),
    )
    _insert_record(connection, RECORD_ID, 30)
    connection.execute(
        "INSERT INTO research_batch_plans VALUES (?, '{}', '2025-02-01')",
        (PLAN_ID,),
    )
    connection.execute(
        "INSERT INTO research_batch_jobs VALUES "
        "(?, ?, 1, '{}', 'completed', 1, NULL, NULL, NULL, ?, ?, "
        "'2025-02-01', '2025-02-01')",
        (JOB_ID, PLAN_ID, RECORD_ID, RECORD_ID),
    )
    connection.commit()
    connection.close()


def test_fresh_database_accepts_365_and_1095_but_rejects_1096(tmp_path):
    database = Database(str(tmp_path / "fresh.sqlite3"))
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO market_datasets VALUES (?, ?, 'test', 'binance', "
            "'BTC/USDT', '4h', '2024-01-01', '2025-01-01', 5, '[]', '2025-01-01')",
            (DATASET_ID, DATASET_ID),
        )
        _insert_record(connection, "a" * 64, 365)
        _insert_record(connection, "b" * 64, 1095)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_record(connection, "c" * 64, 1096)
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE name = ?",
            (DATE_RANGE_DURATION_MIGRATION,),
        ).fetchone()[0] == 1


def test_legacy_migration_preserves_records_foreign_keys_and_guards(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    _legacy_database(path)
    database = Database(str(path))
    Database(str(path))  # repeated initialization is idempotent

    with database.connection() as connection:
        saved = connection.execute(
            "SELECT duration_days, record_json FROM date_range_backtest_records "
            "WHERE id = ?", (RECORD_ID,),
        ).fetchone()
        assert tuple(saved) == (30, "{}")
        assert connection.execute(
            "SELECT saved_record_id FROM research_batch_jobs WHERE id = ?",
            (JOB_ID,),
        ).fetchone()[0] == RECORD_ID
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        _insert_record(connection, "a" * 64, 365)
        _insert_record(connection, "b" * 64, 1095)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_record(connection, "c" * 64, 1096)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE date_range_backtest_records SET record_json = 'x' "
                "WHERE id = ?", (RECORD_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM date_range_backtest_records WHERE id = ?",
                (RECORD_ID,),
            )
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE name = ?",
            (DATE_RANGE_DURATION_MIGRATION,),
        ).fetchone()[0] == 1


def test_unknown_legacy_constraint_fails_closed(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE date_range_backtest_records (
                id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL,
                dataset_sha256 TEXT NOT NULL, start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 60),
                training_days INTEGER NOT NULL, protocol_version TEXT NOT NULL,
                record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    with pytest.raises(RuntimeError, match="unsupported date-range duration"):
        Database(str(path))
