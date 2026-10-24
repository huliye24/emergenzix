"""CLI for read-only comparison of saved date-range backtest records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .config import Settings
from .date_range_comparison import (
    DateRangeComparisonError,
    compare_saved_date_range_records,
    write_comparison_csv,
)


def _format_table(rows: list[dict[str, Any]]) -> str:
    display_fields = [
        "record_id",
        "protocol_version",
        "dataset_sha256",
        "test_start_at",
        "test_end_at_exclusive",
        "fee_bps_per_side",
        "slippage_bps_per_side",
        "selected_candidate",
        "strategy_type",
        "net_return_pct",
        "benchmark_return_pct",
        "max_drawdown_pct",
        "number_of_trades",
        "win_rate_pct",
        "total_fees",
    ]
    widths = {
        field: max(len(field), *(len(str(row[field])) for row in rows))
        for field in display_fields
    }
    header = " | ".join(field.ljust(widths[field]) for field in display_fields)
    divider = "-+-".join("-" * widths[field] for field in display_fields)
    body = [
        " | ".join(str(row[field]).ljust(widths[field]) for field in display_fields)
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare verified saved date-range backtest records without rerunning data"
    )
    parser.add_argument("record_ids", nargs="+")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    try:
        comparison = compare_saved_date_range_records(
            args.db_path or settings.db_path, args.record_ids
        )
        output = (
            write_comparison_csv(comparison, args.output)
            if args.output is not None
            else None
        )
    except (DateRangeComparisonError, OSError) as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    print(_format_table(comparison["observations"]))
    print(
        "\nInterpretation: rows are independent saved observations, not a controlled "
        "head-to-head experiment; no winner is selected."
    )
    if comparison["different_test_periods"]:
        print("Warning: records use different test periods.")
    if comparison["different_cost_assumptions"]:
        print("Warning: records use different fee or slippage assumptions.")
    if output is not None:
        print(f"CSV: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
