"""Offline integration tests for durable one-task historical batch execution."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.date_range_backtest import create_date_range_backtest
from emergenzix_api.research_batch_plan import (
    WeeklyResearchBatchPlanRequest, plan_weekly_research_batch,
)
from emergenzix_api.research_batch_state import (
    BatchStateError, claim_next_research_job, complete_research_job,
    get_research_batch, register_research_batch,
)
from emergenzix_api.research_batch_worker import run_one_batch_job
from emergenzix_api.schemas import Bar

CLOCK = datetime(2025, 11, 20, tzinfo=timezone.utc)


def setup_batch(tmp_path, dates=("2025-10-21",)):
    db = Database(str(tmp_path / "worker.db"), initial_paper_cash=10_000)
    plan = plan_weekly_research_batch(
        WeeklyResearchBatchPlanRequest(start_dates=list(dates)), now=CLOCK,
    )
    register_research_batch(db, plan, now=CLOCK)
    return db, plan


def offline_archive(monkeypatch):
    import emergenzix_api.date_range_backtest as engine

    def fake_archive(*, start_at: datetime, end_at: datetime):
        count = int((end_at - start_at).total_seconds() // (4 * 3600)) + 1
        bars = []
        for index in range(count):
            close = 80000 + 2500 * math.sin(index * 0.11) + index * 3
            opened = close * (1 + 0.0005 * math.sin(index * 0.07))
            bars.append(Bar(
                timestamp=start_at + timedelta(hours=4 * index),
                open=opened, high=max(opened, close) * 1.003,
                low=min(opened, close) * 0.997, close=close,
                volume=1000 + index,
            ).model_dump(mode="json"))
        return bars

    monkeypatch.setattr(engine, "fetch_binance_btc_4h_archive", fake_archive)


def test_worker_executes_one_real_offline_simulation_and_persists_verification(
    tmp_path, monkeypatch,
) -> None:
    offline_archive(monkeypatch)
    db, plan = setup_batch(tmp_path, ("2025-10-21", "2025-10-28"))
    first = run_one_batch_job(db, plan["batch_id"], worker_id="worker-1")
    assert first["status"] == "completed"
    assert first["orders_created"] is False
    assert len(first["record_id"]) == 64
    assert len(first["record_sha256"]) == 64
    state = get_research_batch(db, plan["batch_id"])
    assert [j["state"] for j in state["jobs"]] == ["completed", "pending"]
    assert state["jobs"][0]["saved_record_id"] == first["record_id"]
    assert state["jobs"][0]["saved_record_sha256"] == first["record_sha256"]
    assert state["jobs"][0]["attempts"] == 1
    assert "lease_token" not in state["jobs"][0]
    assert get_research_batch(Database(db.path), plan["batch_id"]) == state
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM date_range_backtest_records"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_worker_retries_only_three_times_and_redacts_raw_errors(tmp_path) -> None:
    db, plan = setup_batch(tmp_path)

    def bad_engine(*args, **kwargs):
        raise RuntimeError("Bearer SECRET https://internal-db")

    recorded = []
    for _ in range(3):
        out = run_one_batch_job(
            db, plan["batch_id"], worker_id="worker", runner=bad_engine,
        )
        assert out["status"] == "failed"
        assert "SECRET" not in json.dumps(out)
        assert "internal-db" not in json.dumps(out)
        recorded.append(out["retry_state"])
    assert recorded == ["pending", "pending", "failed"]
    assert run_one_batch_job(
        db, plan["batch_id"], worker_id="worker", runner=bad_engine,
    )["status"] == "idle"
    state = get_research_batch(db, plan["batch_id"])["jobs"][0]
    assert state["attempts"] == 3
    assert state["state"] == "failed"
    assert state["saved_record_id"] is None


def test_worker_never_completes_from_forged_result(tmp_path, monkeypatch) -> None:
    offline_archive(monkeypatch)
    db, plan = setup_batch(tmp_path)
    saved, inserted = create_date_range_backtest(
        db, start_date=date(2025, 10, 21),
    )
    assert inserted
    for tamper in ("wrong_digest", "unknown_id", "missing_digest"):
        def fake_runner(*args, **kwargs):
            forged = dict(saved)
            if tamper == "wrong_digest":
                forged["record_sha256"] = "f" * 64
            elif tamper == "unknown_id":
                forged["id"] = "e" * 64
            else:
                forged.pop("record_sha256")
            return forged, False

        out = run_one_batch_job(
            db, plan["batch_id"], worker_id="worker", runner=fake_runner,
        )
        assert out["status"] == "failed"
        assert get_research_batch(db, plan["batch_id"])["jobs"][0]["state"] != "completed"


def test_lease_fencing_blocks_stale_completion_of_real_saved_result(
    tmp_path, monkeypatch,
) -> None:
    offline_archive(monkeypatch)
    db, plan = setup_batch(tmp_path)
    saved, _ = create_date_range_backtest(
        db, start_date=date(2025, 10, 21),
    )
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lease = claim_next_research_job(
        db, plan["batch_id"], worker_id="A", now=moment, lease_seconds=30,
    )
    with pytest.raises(BatchStateError, match="expired"):
        complete_research_job(
            db, lease["planned_job_id"], lease["lease_token"],
            record_id=saved["id"], record_sha256=saved["record_sha256"],
            now=moment + timedelta(seconds=30),
        )
    with pytest.raises(BatchStateError, match="expired"):
        complete_research_job(
            db, lease["planned_job_id"], "0" * 32,
            record_id=saved["id"], record_sha256=saved["record_sha256"],
            now=moment + timedelta(seconds=1),
        )
    assert get_research_batch(db, plan["batch_id"])["jobs"][0]["state"] == "leased"


def test_retry_can_reuse_immutable_saved_result_after_interruption(
    tmp_path, monkeypatch,
) -> None:
    offline_archive(monkeypatch)
    db, plan = setup_batch(tmp_path)
    saved, _ = create_date_range_backtest(db, start_date=date(2025, 10, 21))
    out = run_one_batch_job(db, plan["batch_id"], worker_id="worker")
    assert out["status"] == "completed"
    assert out["existing_immutable_record"] is True
    assert out["record_id"] == saved["id"]
    assert get_research_batch(db, plan["batch_id"])["jobs"][0]["attempts"] == 1


def test_old_batch_database_is_migrated_without_erasing_task_state(tmp_path) -> None:
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE research_batch_plans (
                id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE research_batch_jobs (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES research_batch_plans(id),
                position INTEGER NOT NULL, request_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'pending', 'leased', 'failed', 'cancelled'
                )),
                attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
                lease_expires_at TEXT, last_error_code TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(batch_id, position)
            );
        """)
        conn.execute(
            "INSERT INTO research_batch_plans VALUES (?, ?, ?)",
            ("a" * 64, "{}", "2025-10-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO research_batch_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("b" * 64, "a" * 64, 1, "{}", "failed", 2, None, None,
             "worker_error", "2025-10-01T00:00:00Z", "2025-10-02T00:00:00Z"),
        )
    db = Database(str(db_path))
    with db.connection() as conn:
        row = conn.execute(
            "SELECT state, attempts, last_error_code, saved_record_id "
            "FROM research_batch_jobs WHERE id = ?", ("b" * 64,),
        ).fetchone()
        assert (row["state"], row["attempts"], row["last_error_code"]) == (
            "failed", 2, "worker_error",
        )
        assert row["saved_record_id"] is None
    Database(str(db_path))  # Reinitialization must not rebuild again.


def test_no_http_batch_execution_is_exposed(client) -> None:
    assert client.post(
        "/api/v1/research/date-range-batches/execute", json={}
    ).status_code == 404
