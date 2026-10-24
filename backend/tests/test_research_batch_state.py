"""Durability, atomicity, stale-token and retry-bound tests (no worker execution)."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.research_batch_plan import (
    WeeklyResearchBatchPlanRequest,
    plan_weekly_research_batch,
)
from emergenzix_api.research_batch_state import (
    BatchStateError, cancel_unclaimed_research_jobs,
    claim_next_research_job, fail_research_job, get_research_batch,
    register_research_batch, renew_research_job_lease,
)

CLOCK = datetime(2025, 11, 20, tzinfo=timezone.utc)


def _setup(tmp_path, dates=("2025-10-21", "2025-10-28")):
    db = Database(str(tmp_path / "emergenzix-test.db"), initial_paper_cash=10000)
    plan = plan_weekly_research_batch(
        WeeklyResearchBatchPlanRequest(start_dates=list(dates)),
        now=CLOCK,
    )
    return db, plan


def test_register_is_atomic_idempotent_and_survives_reconnect(tmp_path) -> None:
    db, plan = _setup(tmp_path)
    first = register_research_batch(db, plan, now=CLOCK)
    second = register_research_batch(db, plan, now=CLOCK + timedelta(seconds=1))
    assert first == second
    assert first["batch_id"] == plan["batch_id"]
    assert first["job_count"] == 2
    assert [job["sequence"] for job in first["jobs"]] == [1, 2]
    assert {job["state"] for job in first["jobs"]} == {"pending"}
    assert first["live_order_permission"] is False

    reopened = Database(db.path)
    assert get_research_batch(reopened, plan["batch_id"]) == first
    with reopened.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_batch_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_batch_jobs").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM date_range_backtest_records").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE research_batch_plans SET plan_json = ? WHERE id = ?",
                ("{}", plan["batch_id"]),
            )


@pytest.mark.parametrize("field", [
    "batch_id", "plan_sha256", "periods_count", "orders_created",
])
def test_register_rejects_tampered_manifests_before_writing(tmp_path, field) -> None:
    db, original = _setup(tmp_path)
    plan = dict(original)
    plan[field] = "f" * 64 if field in {"batch_id", "plan_sha256"} else 99
    with pytest.raises(BatchStateError):
        register_research_batch(db, plan, now=CLOCK)
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_batch_plans").fetchone()[0] == 0


def test_atomic_claim_one_job_per_batch_and_random_tokens(tmp_path) -> None:
    db, plan = _setup(tmp_path)
    register_research_batch(db, plan, now=CLOCK)
    first = claim_next_research_job(db, plan["batch_id"], worker_id="worker-A", now=CLOCK)
    assert first["sequence"] == 1
    assert len(first["lease_token"]) == 32
    assert first["attempt"] == 1
    assert first["orders_allowed"] is False
    assert claim_next_research_job(db, plan["batch_id"], worker_id="worker-B", now=CLOCK) is None
    snapshot = get_research_batch(db, plan["batch_id"])
    assert snapshot["jobs"][0]["state"] == "leased"
    assert snapshot["jobs"][0]["attempts"] == 1
    assert "lease_token" not in snapshot["jobs"][0]
    assert snapshot["jobs"][1]["state"] == "pending"


def test_two_connections_cannot_claim_same_job(tmp_path) -> None:
    db, plan = _setup(tmp_path)
    register_research_batch(db, plan, now=CLOCK)

    def try_claim(worker):
        second_connection = Database(db.path)
        return claim_next_research_job(
            second_connection, plan["batch_id"], worker_id=worker, now=CLOCK
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(try_claim, ("alpha", "bravo")))
    assert sum(item is not None for item in result) == 1
    assert get_research_batch(db, plan["batch_id"])["jobs"][0]["attempts"] == 1


def test_lease_expiry_recovers_then_old_worker_is_fenced(tmp_path) -> None:
    db, plan = _setup(tmp_path, dates=("2025-10-21",))
    register_research_batch(db, plan, now=CLOCK)
    first = claim_next_research_job(db, plan["batch_id"], worker_id="A", now=CLOCK)
    expired = CLOCK + timedelta(seconds=300)
    recovered = claim_next_research_job(db, plan["batch_id"], worker_id="B", now=expired)
    assert recovered["planned_job_id"] == first["planned_job_id"]
    assert recovered["attempt"] == 2
    assert recovered["lease_token"] != first["lease_token"]
    with pytest.raises(BatchStateError, match="expired or superseded"):
        fail_research_job(
            db, first["planned_job_id"], first["lease_token"],
            error_code="worker_error", now=expired,
        )
    with pytest.raises(BatchStateError, match="not extendable"):
        renew_research_job_lease(
            db, first["planned_job_id"], first["lease_token"], now=expired,
        )
    assert get_research_batch(db, plan["batch_id"])["jobs"][0]["state"] == "leased"


def test_heartbeat_extends_current_lease_but_rejects_stale_or_shortening(tmp_path) -> None:
    db, plan = _setup(tmp_path, dates=("2025-10-21",))
    register_research_batch(db, plan, now=CLOCK)
    job = claim_next_research_job(db, plan["batch_id"], worker_id="A", now=CLOCK)
    extended = renew_research_job_lease(
        db, job["planned_job_id"], job["lease_token"],
        now=CLOCK + timedelta(seconds=200), lease_seconds=300,
    )
    assert extended.endswith("Z")
    with pytest.raises(BatchStateError, match="not extendable"):
        renew_research_job_lease(
            db, job["planned_job_id"], job["lease_token"],
            now=CLOCK + timedelta(seconds=201), lease_seconds=30,
        )
    assert fail_research_job(
        db, job["planned_job_id"], job["lease_token"], error_code="worker_error",
        now=CLOCK + timedelta(seconds=301),
    ) == "pending"


def test_bounded_retry_exhaustion_and_expired_third_attempt(tmp_path) -> None:
    db, plan = _setup(tmp_path, dates=("2025-10-21",))
    register_research_batch(db, plan, now=CLOCK)
    for attempt in range(1, 4):
        job = claim_next_research_job(
            db, plan["batch_id"], worker_id="worker", now=CLOCK + timedelta(seconds=attempt)
        )
        assert job["attempt"] == attempt
        result = fail_research_job(
            db, job["planned_job_id"], job["lease_token"],
            error_code="data_incomplete", now=CLOCK + timedelta(seconds=attempt),
        )
        assert result == ("failed" if attempt == 3 else "pending")
    assert claim_next_research_job(
        db, plan["batch_id"], worker_id="worker", now=CLOCK + timedelta(seconds=10)
    ) is None
    snapshot = get_research_batch(db, plan["batch_id"])
    assert snapshot["jobs"][0]["attempts"] == 3
    assert snapshot["jobs"][0]["last_error_code"] == "data_incomplete"
    assert snapshot["jobs"][0]["state"] == "failed"

    second_db, other_plan = _setup(tmp_path / "other", dates=("2025-10-21",))
    register_research_batch(second_db, other_plan, now=CLOCK)
    third = None
    for attempt in range(3):
        third = claim_next_research_job(
            second_db, other_plan["batch_id"], worker_id="worker",
            now=CLOCK + timedelta(seconds=300 * attempt),
        )
        assert third is not None
    assert claim_next_research_job(
        second_db, other_plan["batch_id"], worker_id="worker",
        now=CLOCK + timedelta(seconds=900),
    ) is None
    assert get_research_batch(second_db, other_plan["batch_id"])["jobs"][0]["state"] == "failed"


def test_cancel_pending_does_not_preempt_running_lease(tmp_path) -> None:
    db, plan = _setup(tmp_path)
    register_research_batch(db, plan, now=CLOCK)
    leased = claim_next_research_job(db, plan["batch_id"], worker_id="A", now=CLOCK)
    assert cancel_unclaimed_research_jobs(db, plan["batch_id"], now=CLOCK) == 1
    snapshot = get_research_batch(db, plan["batch_id"])
    assert [job["state"] for job in snapshot["jobs"]] == ["leased", "cancelled"]
    assert claim_next_research_job(db, plan["batch_id"], worker_id="B", now=CLOCK) is None
    assert fail_research_job(
        db, leased["planned_job_id"], leased["lease_token"],
        error_code="worker_error", now=CLOCK,
    ) == "pending"
    assert cancel_unclaimed_research_jobs(db, plan["batch_id"], now=CLOCK) == 1
    assert all(job["state"] == "cancelled" for job in get_research_batch(
        db, plan["batch_id"]
    )["jobs"])


def test_invalid_inputs_cannot_acquire_or_acknowledge_leases(tmp_path) -> None:
    db, plan = _setup(tmp_path)
    register_research_batch(db, plan, now=CLOCK)
    with pytest.raises(BatchStateError):
        claim_next_research_job(db, plan["batch_id"], worker_id="../secret", now=CLOCK)
    with pytest.raises(BatchStateError):
        claim_next_research_job(db, plan["batch_id"], worker_id="worker", lease_seconds=1, now=CLOCK)
    job = claim_next_research_job(db, plan["batch_id"], worker_id="worker", now=CLOCK)
    with pytest.raises(BatchStateError):
        fail_research_job(db, job["planned_job_id"], job["lease_token"],
                          error_code="full secret url in trace", now=CLOCK)
    with pytest.raises(BatchStateError):
        renew_research_job_lease(db, job["planned_job_id"], "0" * 32, now=CLOCK)
    assert get_research_batch(db, plan["batch_id"])["jobs"][0]["state"] == "leased"
