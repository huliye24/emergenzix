"""Private batch-operator CLI: plan, register and inspect weekly research jobs.

No public endpoint, market data download, backtest execution or exchange orders.
The explicit 'register' subcommand is the only database mutation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from .config import Settings
from .database import Database
from .research_batch_plan import (
    WeeklyResearchBatchPlanRequest,
    plan_weekly_research_batch,
)
from .research_batch_report import (
    BatchReportError,
    build_research_batch_report_from_file,
)
from .research_batch_state import BatchStateError, register_research_batch


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan, register or inspect historical BTC/USDT research weeks"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    for command in ("plan", "register"):
        item = commands.add_parser(
            command,
            help="Dry-run a plan" if command == "plan" else "Save jobs without running them",
        )
        item.add_argument(
            "--start-date", dest="start_dates", action="append", required=True,
            metavar="YYYY-MM-DD", help="Repeat for each non-overlapping week (max 12)",
        )
        item.add_argument("--training-days", type=int, default=90)
        item.add_argument("--initial-cash", type=float, default=10_000.0)
        item.add_argument("--fee-bps", type=float, default=10.0)
        item.add_argument("--slippage-bps", type=float, default=5.0)
        if command == "register":
            item.add_argument("--db-path", type=Path, default=None)

    status = commands.add_parser(
        "status", help="Inspect saved batch and completed-record checks read-only"
    )
    status.add_argument("--batch-id", required=True)
    status.add_argument("--db-path", type=Path, default=None)
    return parser


def _request(args: argparse.Namespace) -> WeeklyResearchBatchPlanRequest:
    return WeeklyResearchBatchPlanRequest.model_validate({
        "start_dates": args.start_dates,
        "training_days": args.training_days,
        "initial_cash": args.initial_cash,
        "fee_bps": args.fee_bps,
        "slippage_bps": args.slippage_bps,
        "duration_days": 7,
    })


def _db_path(override: Path | None) -> str:
    # Resolve settings only when an existing or new database is actually needed.
    if override is not None:
        return str(override.expanduser())
    return Settings.from_env().db_path


def _plan_output(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "planned_only",
        "batch_id": plan["batch_id"],
        "plan_sha256": plan["plan_sha256"],
        "periods_count": plan["periods_count"],
        "plan_persisted": False,
        "market": plan["selection"]["market"],
        "periods": [
            {
                "sequence": period["sequence"],
                "start_date": period["backtest_request"]["start_date"],
                "training_start_at": period["training_start_at"],
                "test_end_at_exclusive": period["test_end_at_exclusive"],
                "planned_job_id": period["planned_job_id"],
            }
            for period in plan["periods"]
        ],
        "orders_created": False,
    }


def _register_output(saved: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "registered_or_already_present",
        "batch_id": saved["batch_id"],
        "job_count": saved["job_count"],
        "jobs": [
            {
                "sequence": job["sequence"],
                "planned_job_id": job["planned_job_id"],
                "state": job["state"],
                "attempts": job["attempts"],
            }
            for job in saved["jobs"]
        ],
        "orders_created": False,
        "live_order_permission": False,
    }


def _status_output(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["state"],
        "batch_id": report["batch_id"],
        "total_planned": report["total_planned"],
        "state_counts": report["state_counts"],
        "completion_rate_pct": report["completion_rate_pct"],
        "snapshot_sha256": report["snapshot_sha256"],
        "jobs": [
            {
                "sequence": job["sequence"],
                "planned_job_id": job["planned_job_id"],
                "state": job["state"],
                "attempts": job["attempts"],
                "error_code": job["error_code"],
                "saved_record_id": job["saved_record_id"],
            }
            for job in report["jobs"]
        ],
        "orders_created": False,
        "live_order_permission": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in ("plan", "register"):
            manifest = plan_weekly_research_batch(_request(args))
            if args.command == "plan":
                output = _plan_output(manifest)
            else:
                # Database constructor may initialize/migrate the supplied file.
                # No other command in this CLI permits that side effect.
                db = Database(_db_path(args.db_path))
                output = _register_output(register_research_batch(db, manifest))
        else:
            # Reuse the strictly read-only verified report path. Do not open
            # Database(...), which could initialize a nonexistent SQLite file.
            report = build_research_batch_report_from_file(
                _db_path(args.db_path), args.batch_id,
            )
            output = _status_output(report)
    except (
        ValidationError, BatchStateError, BatchReportError,
        ValueError, TypeError, OverflowError, sqlite3.Error, OSError,
    ):
        # Do not leak SQLite filenames, payloads, archive URLs or credentials.
        print(json.dumps({
            "status": "error",
            "error_code": "invalid_batch_request"
            if args.command in ("plan", "register")
            else "batch_status_unavailable",
        }), file=sys.stderr)
        return 2

    print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
