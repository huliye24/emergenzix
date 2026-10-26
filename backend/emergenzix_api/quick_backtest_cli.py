"""Two-input BTC research launcher: capital + duration, optional historical start.

Uses the existing immutable date-range backtest and its report exporter.
Never invokes exchange order placement or the unvalidated S-003 tranche logic.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from .date_range_backtest_cli import main as run_legacy_date_range
from .quick_backtest import resolve_quick_backtest


def _parse_start(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise argparse.ArgumentTypeError("start must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("start must be YYYY-MM-DD") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Quick historical BTC/USDT backtest (no trading orders)"
    )
    parser.add_argument("--capital", type=float, required=True, help="initial USDT")
    parser.add_argument(
        "--period", required=True,
        help="1w=7d, 1m=30d, 1y=365d, 3y=1095d, or custom Nd",
    )
    parser.add_argument(
        "--start", type=_parse_start, default=None,
        help="UTC start YYYY-MM-DD; omitted = last complete UTC date range",
    )
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        start, duration_days = resolve_quick_backtest(
            initial_cash=args.capital, period=args.period, start_date=args.start,
        )
    except ValueError as exc:
        print(
            json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

    output = (
        args.output if args.output is not None
        else Path("out") / (
            f"quick-{start.isoformat()}-{duration_days}d-{args.capital:g}usdt.json"
        )
    )
    call = [
        "--start-date", start.isoformat(),
        "--duration-days", str(duration_days),
        "--initial-cash", str(args.capital),
        "--output", str(output),
    ]
    if args.db_path is not None:
        call.extend(["--db-path", args.db_path])
    return run_legacy_date_range(call)


if __name__ == "__main__":
    raise SystemExit(main())
