"""Execute at most one leased historical research job; never place orders.

This module is an explicit private CLI, not a public web endpoint or
unsupervised scheduler. Results are acknowledged only after SQLite integrity
and request-identity validation.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import uuid
from datetime import date
from typing import Any, Callable, Sequence

from .config import Settings
from .database import Database
from .date_range_backtest import create_date_range_backtest
from .historical_data import HistoricalArchiveUnavailable
from .research_batch_state import (
    BatchStateError, claim_next_research_job, complete_research_job,
    fail_research_job, renew_research_job_lease,
)

Runner = Callable[..., tuple[dict[str, Any], bool]]


def _safe_fail(db: Database, job_id: str, token: str, code: str) -> str:
    try:
        return fail_research_job(db, job_id, token, error_code=code)
    except (BatchStateError, sqlite3.Error):
        return "lease_lost"


def run_one_batch_job(
    db: Database, batch_id: str, *, worker_id: str,
    lease_seconds: int = 300, runner: Runner | None = None,
) -> dict[str, Any]:
    """One atomic claim, one historical run, one fenced success/failure."""
    claim = claim_next_research_job(
        db, batch_id, worker_id=worker_id, lease_seconds=lease_seconds
    )
    if claim is None:
        return {
            "status": "idle", "batch_id": batch_id,
            "jobs_run": 0, "orders_created": False,
        }

    job_id = claim["planned_job_id"]
    token = claim["lease_token"]
    stop = threading.Event()
    lost = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(max(10, lease_seconds // 3)):
            try:
                renew_research_job_lease(
                    db, job_id, token, lease_seconds=lease_seconds
                )
            except (BatchStateError, sqlite3.Error):
                lost.set()
                return

    thread = threading.Thread(
        target=heartbeat, name="research-batch-heartbeat", daemon=True
    )
    thread.start()
    saved: dict[str, Any] | None = None
    inserted = False
    failure: str | None = None
    try:
        request = claim["backtest_request"]
        if not isinstance(request, dict) or request.get("duration_days") != 7:
            raise ValueError("invalid approved job")
        perform = runner if runner is not None else create_date_range_backtest
        saved, inserted = perform(
            db,
            start_date=date.fromisoformat(request["start_date"]),
            duration_days=request["duration_days"],
            training_days=request["training_days"],
            initial_cash=request["initial_cash"],
            fee_bps=request["fee_bps"],
            slippage_bps=request["slippage_bps"],
        )
    except HistoricalArchiveUnavailable:
        failure = "archive_unavailable"
    except ValueError:
        failure = "data_incomplete"
    except Exception:
        # Raw exceptions can include credentials or local service URLs.
        failure = "worker_error"
    finally:
        stop.set()
        thread.join(timeout=16)

    if lost.is_set() or thread.is_alive():
        return {
            "status": "lease_lost", "batch_id": batch_id,
            "planned_job_id": job_id, "orders_created": False,
        }

    if failure is None:
        try:
            if not isinstance(saved, dict):
                raise BatchStateError("invalid result from historical engine")
            complete_research_job(
                db, job_id, token,
                record_id=saved.get("id"),
                record_sha256=saved.get("record_sha256"),
            )
        except (BatchStateError, sqlite3.Error, ValueError, TypeError):
            failure = "worker_error"
        else:
            return {
                "status": "completed", "batch_id": batch_id,
                "planned_job_id": job_id, "record_id": saved["id"],
                "record_sha256": saved["record_sha256"],
                "existing_immutable_record": not inserted,
                "orders_created": False,
            }

    retry_state = _safe_fail(db, job_id, token, failure or "worker_error")
    return {
        "status": "lease_lost" if retry_state == "lease_lost" else "failed",
        "batch_id": batch_id, "planned_job_id": job_id,
        "retry_state": None if retry_state == "lease_lost" else retry_state,
        "error_code": failure, "orders_created": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run ONE approved historical research job (no orders)"
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--execute-one", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_one:
        parser.error("explicit --execute-one is required")
    if not re.fullmatch(r"[0-9a-f]{64}", args.batch_id):
        parser.error("--batch-id must be a lowercase 64-character SHA-256")
    worker_id = args.worker_id or ("local-" + uuid.uuid4().hex[:12])
    try:
        settings = Settings.from_env()
        db = Database(
            args.db_path or settings.db_path,
            initial_paper_cash=settings.initial_paper_cash,
        )
        result = run_one_batch_job(
            db, args.batch_id, worker_id=worker_id
        )
    except (BatchStateError, sqlite3.Error, OSError, ValueError):
        print(json.dumps({
            "status": "error", "error_code": "batch_worker_unavailable"
        }), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in ("completed", "idle") else 2


if __name__ == "__main__":
    raise SystemExit(main())
