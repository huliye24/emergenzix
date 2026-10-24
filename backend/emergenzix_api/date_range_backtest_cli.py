"""Command-line entry point for the BTC date-range backtest workflow."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from .config import Settings
from .database import Database
from .date_range_backtest import create_date_range_backtest
from .date_range_report import write_report_artifacts
from .historical_data import HistoricalArchiveUnavailable


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a reproducible BTC one-week historical strategy experiment"
    )
    parser.add_argument("--start-date", required=True, type=_parse_date)
    parser.add_argument("--duration-days", type=int, default=7)
    parser.add_argument("--training-days", type=int, default=90)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        database = Database(
            args.db_path or settings.db_path,
            initial_paper_cash=settings.initial_paper_cash,
        )
        record, inserted = create_date_range_backtest(
            database,
            start_date=args.start_date,
            duration_days=args.duration_days,
            training_days=args.training_days,
            initial_cash=args.initial_cash,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
    except (ValueError, HistoricalArchiveUnavailable, RuntimeError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    output = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output is not None:
        try:
            artifacts = write_report_artifacts(record, args.output)
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "created" if inserted else "already_exists",
                    "record_id": record["id"],
                    "record_sha256": record["record_sha256"],
                    "artifacts": artifacts,
                    "database": args.db_path or settings.db_path,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
