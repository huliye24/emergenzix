"""SQLite-backed batch task ledger: registration, atomic leases and bounded retries.

Internal service only: not exposed through unauthenticated FastAPI routes.
No market-data calls, backtest execution, order execution, or success marking.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from .database import Database
from .research_batch_plan import (
    WeeklyResearchBatchPlanRequest,
    plan_weekly_research_batch,
)

MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 300
_ALLOWED_FAILURES = frozenset({"archive_unavailable", "data_incomplete", "worker_error"})


class BatchStateError(ValueError):
    """Invalid plan, missing batch or lost/stale lease; no sensitive error details."""


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _sha(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _instant(value: datetime | None = None) -> datetime:
    result = datetime.now(timezone.utc) if value is None else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise BatchStateError("timestamps must be timezone aware")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    # Fixed width makes lexical comparisons safe for expired leases.
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_plan(plan: object, *, now: datetime) -> tuple[str, str, list[dict[str, Any]]]:
    """Independently reproduce job IDs and manifest before making SQL changes."""
    if not isinstance(plan, dict):
        raise BatchStateError("invalid research batch manifest")
    config = plan.get("selection")
    if not isinstance(config, dict):
        raise BatchStateError("invalid research batch manifest")
    keys = (
        "start_dates", "duration_days", "training_days",
        "initial_cash", "fee_bps", "slippage_bps",
    )
    try:
        payload = {key: config[key] for key in keys}
        request = WeeklyResearchBatchPlanRequest.model_validate(payload)
        expected = plan_weekly_research_batch(request, now=now)
        if _json(expected) != _json(plan):
            raise BatchStateError("research batch manifest does not match its fingerprint")
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise BatchStateError("invalid or altered research batch manifest") from exc
    return expected["batch_id"], _json(expected), expected["periods"]


def _batch_exists(conn: sqlite3.Connection, batch_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM research_batch_plans WHERE id = ?", (batch_id,)
    ).fetchone() is None:
        raise BatchStateError("research batch not found")


def register_research_batch(
    db: Database, plan: dict[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a validated v1 plan and all its pending jobs as one transaction.

    Identical re-registration is idempotent and does not reset job state.
    """
    moment = _instant(now)
    batch_id, manifest_json, periods = _validate_plan(plan, now=moment)
    timestamp = _iso(moment)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT plan_json FROM research_batch_plans WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is not None:
            if row["plan_json"] != manifest_json:
                raise BatchStateError("research batch manifest ID collision")
            existing = conn.execute(
                "SELECT id, position, request_json FROM research_batch_jobs "
                "WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
            expected_jobs = [
                (period["planned_job_id"], period["sequence"], _json(period["backtest_request"]))
                for period in periods
            ]
            if [
                (job["id"], job["position"], job["request_json"]) for job in existing
            ] != expected_jobs:
                raise BatchStateError("existing batch jobs conflict with research manifest")
        else:
            conn.execute(
                "INSERT INTO research_batch_plans (id, plan_json, created_at) VALUES (?, ?, ?)",
                (batch_id, manifest_json, timestamp),
            )
            for period in periods:
                conn.execute(
                    "INSERT INTO research_batch_jobs "
                    "(id, batch_id, position, request_json, state, attempts, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
                    (
                        period["planned_job_id"], batch_id, period["sequence"],
                        _json(period["backtest_request"]), timestamp, timestamp,
                    ),
                )
    return get_research_batch(db, batch_id)


def get_research_batch(db: Database, batch_id: str) -> dict[str, Any]:
    """Read registered state; intentionally never expose active lease tokens."""
    if not _sha(batch_id):
        raise BatchStateError("invalid research batch ID")
    with db.connection() as conn:
        plan = conn.execute(
            "SELECT id, plan_json, created_at FROM research_batch_plans WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if plan is None:
            raise BatchStateError("research batch not found")
        jobs = conn.execute(
            "SELECT id, position, request_json, state, attempts, lease_expires_at, "
            "last_error_code FROM research_batch_jobs WHERE batch_id = ? ORDER BY position",
            (batch_id,),
        ).fetchall()
    manifest = json.loads(plan["plan_json"])
    if len(jobs) != manifest["periods_count"]:
        raise BatchStateError("persisted research batch is incomplete")
    return {
        "batch_id": batch_id,
        "plan": manifest,
        "created_at": plan["created_at"],
        "job_count": len(jobs),
        "jobs": [
            {
                "planned_job_id": row["id"], "sequence": row["position"],
                "backtest_request": json.loads(row["request_json"]),
                "state": row["state"], "attempts": row["attempts"],
                "lease_expires_at": row["lease_expires_at"],
                "last_error_code": row["last_error_code"],
            }
            for row in jobs
        ],
        "automatic_strategy_promotion": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
    }


def _lease_seconds(value: int) -> None:
    if type(value) is not int or not 30 <= value <= 1800:
        raise BatchStateError("lease duration must be 30 to 1800 seconds")


def _validate_token(token: str) -> None:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise BatchStateError("invalid lease token")


def _expire_leases(conn: sqlite3.Connection, batch_id: str, timestamp: str) -> None:
    """Recover expired work atomically; a stale attempt can never be acknowledged."""
    conn.execute(
        "UPDATE research_batch_jobs SET state = CASE WHEN attempts < ? THEN 'pending' "
        "ELSE 'failed' END, lease_token = NULL, lease_expires_at = NULL, "
        "last_error_code = 'lease_expired', updated_at = ? "
        "WHERE batch_id = ? AND state = 'leased' AND lease_expires_at <= ?",
        (MAX_ATTEMPTS, timestamp, batch_id, timestamp),
    )


def claim_next_research_job(
    db: Database, batch_id: str, *, worker_id: str,
    now: datetime | None = None, lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any] | None:
    """Atomically lease one job per batch, in planned order, without executing it."""
    if not _sha(batch_id):
        raise BatchStateError("invalid research batch ID")
    if not isinstance(worker_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", worker_id):
        raise BatchStateError("invalid worker identity")
    _lease_seconds(lease_seconds)
    moment = _instant(now)
    timestamp = _iso(moment)
    expiry = _iso(moment + timedelta(seconds=lease_seconds))
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _batch_exists(conn, batch_id)
        _expire_leases(conn, batch_id, timestamp)
        if conn.execute(
            "SELECT 1 FROM research_batch_jobs WHERE batch_id = ? AND state = 'leased' LIMIT 1",
            (batch_id,),
        ).fetchone():
            return None  # Prevent two jobs from one batch executing simultaneously.
        row = conn.execute(
            "SELECT id, position, request_json, attempts FROM research_batch_jobs "
            "WHERE batch_id = ? AND state = 'pending' ORDER BY position LIMIT 1",
            (batch_id,),
        ).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        changed = conn.execute(
            "UPDATE research_batch_jobs SET state = 'leased', attempts = attempts + 1, "
            "lease_token = ?, lease_expires_at = ?, updated_at = ?, last_error_code = NULL "
            "WHERE id = ? AND state = 'pending' AND attempts < ?",
            (token, expiry, timestamp, row["id"], MAX_ATTEMPTS),
        )
        if changed.rowcount != 1:
            raise BatchStateError("research batch lease was contested")
        return {
            "batch_id": batch_id,
            "planned_job_id": row["id"],
            "sequence": row["position"],
            "backtest_request": json.loads(row["request_json"]),
            "attempt": row["attempts"] + 1,
            "lease_token": token,
            "lease_expires_at": expiry,
            "orders_allowed": False,
        }


def renew_research_job_lease(
    db: Database, job_id: str, token: str, *,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> str:
    """Renew only a currently live lease with the same unguessable token."""
    if not _sha(job_id):
        raise BatchStateError("invalid research job ID")
    _validate_token(token)
    _lease_seconds(lease_seconds)
    moment = _instant(now)
    timestamp = _iso(moment)
    new_expiry = _iso(moment + timedelta(seconds=lease_seconds))
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE research_batch_jobs SET lease_expires_at = ?, updated_at = ? "
            "WHERE id = ? AND state = 'leased' AND lease_token = ? "
            "AND lease_expires_at > ? AND lease_expires_at < ?",
            (new_expiry, timestamp, job_id, token, timestamp, new_expiry),
        )
        if changed.rowcount != 1:
            raise BatchStateError("research job lease is missing, expired or not extendable")
    return new_expiry


def fail_research_job(
    db: Database, job_id: str, token: str, *, error_code: str,
    now: datetime | None = None,
) -> str:
    """Record a sanitized failure and retry at most twice (three attempts total)."""
    if not _sha(job_id):
        raise BatchStateError("invalid research job ID")
    _validate_token(token)
    if error_code not in _ALLOWED_FAILURES:
        raise BatchStateError("invalid sanitized failure code")
    timestamp = _iso(_instant(now))
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE research_batch_jobs SET state = CASE WHEN attempts < ? "
            "THEN 'pending' ELSE 'failed' END, "
            "lease_token = NULL, lease_expires_at = NULL, last_error_code = ?, "
            "updated_at = ? WHERE id = ? AND state = 'leased' AND lease_token = ? "
            "AND lease_expires_at > ?",
            (MAX_ATTEMPTS, error_code, timestamp, job_id, token, timestamp),
        )
        if changed.rowcount != 1:
            raise BatchStateError("research job lease is missing, expired or superseded")
        state = conn.execute(
            "SELECT state FROM research_batch_jobs WHERE id = ?", (job_id,)
        ).fetchone()["state"]
    return state


def cancel_unclaimed_research_jobs(
    db: Database, batch_id: str, *, now: datetime | None = None,
) -> int:
    """Prevent future claims; a still-leased job is not preempted."""
    if not _sha(batch_id):
        raise BatchStateError("invalid research batch ID")
    timestamp = _iso(_instant(now))
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _batch_exists(conn, batch_id)
        # Include expired leases so they can be cancelled instead of re-claimed.
        _expire_leases(conn, batch_id, timestamp)
        changed = conn.execute(
            "UPDATE research_batch_jobs SET state = 'cancelled', updated_at = ? "
            "WHERE batch_id = ? AND state = 'pending'",
            (timestamp, batch_id),
        )
        return changed.rowcount
