"""Read-only cross-period statistics for saved BTC date-range research records.

Each observation is a separate historical experiment, not an investable return
stream. No weekly observations are compounded or labeled independent trials.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean, median
from typing import Any
from urllib.parse import quote

from .date_range_comparison import DateRangeComparisonError, _comparison_row

STATISTICS_VERSION = "date-range-statistics-v1"
SUPPORTED_PROTOCOL = "btc-date-range-backtest-v4"


class DateRangeStatisticsError(ValueError):
    """Public error kind for read-only screening; never includes database paths."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise DateRangeStatisticsError("invalid_record")
    return value


def _number(value: object, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if type(value) not in {float, int}:
        raise DateRangeStatisticsError("invalid_record")
    number = float(value)
    if not math.isfinite(number):
        raise DateRangeStatisticsError("invalid_record")
    if minimum is not None and number < minimum:
        raise DateRangeStatisticsError("invalid_record")
    if maximum is not None and number > maximum:
        raise DateRangeStatisticsError("invalid_record")
    return number


def _timestamp(value: object) -> datetime:
    """Accept canonical UTC Z timestamps only; avoid lexical date ordering."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DateRangeStatisticsError("invalid_record")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DateRangeStatisticsError("invalid_record") from exc
    if result.utcoffset() != timedelta(0):
        raise DateRangeStatisticsError("invalid_record")
    if any((result.hour, result.minute, result.second, result.microsecond)):
        raise DateRangeStatisticsError("invalid_record")
    return result


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "mean": round(fmean(values), 8),
        "median": round(median(values), 8),
        "minimum": round(min(values), 8),
        "maximum": round(max(values), 8),
    }


def summarize_saved_date_range_records(
    db_path: str | Path, *, limit: int = 30
) -> dict[str, Any]:
    """Summarize the latest N verified saved records in immutable creation order.

    The read-only SQLite query and hash verification take place under a single
    connection; one damaged or unsupported record aborts the full response.
    """
    if type(limit) is not int or not 2 <= limit <= 100:
        raise DateRangeStatisticsError("invalid_limit")

    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise DateRangeStatisticsError("database_unavailable")
    uri = "file:" + quote(resolved.as_posix(), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=15.0) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                "SELECT * FROM date_range_backtest_records "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            observations = [_comparison_row(row) for row in rows]
    except DateRangeComparisonError as exc:
        raise DateRangeStatisticsError("invalid_record") from exc
    except (sqlite3.Error, OSError) as exc:
        raise DateRangeStatisticsError("database_unavailable") from exc

    windows: list[tuple[datetime, datetime]] = []
    durations: list[int] = []
    for item in observations:
        if item["protocol_version"] != SUPPORTED_PROTOCOL:
            raise DateRangeStatisticsError("invalid_record")
        record_id = item["record_id"]
        digest = item["dataset_sha256"]
        if (
            not isinstance(record_id, str)
            or len(record_id) != 64
            or any(character not in "0123456789abcdef" for character in record_id)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(item["selected_candidate"], str)
            or not item["selected_candidate"]
            or not isinstance(item["strategy_type"], str)
            or not item["strategy_type"]
        ):
            raise DateRangeStatisticsError("invalid_record")

        start = _timestamp(item["test_start_at"])
        end = _timestamp(item["test_end_at_exclusive"])
        span = end - start
        if span <= timedelta(0) or span > timedelta(days=30) or span % timedelta(days=1):
            raise DateRangeStatisticsError("invalid_record")
        windows.append((start, end))
        durations.append(span.days)

        _number(item["net_return_pct"], minimum=-100)
        _number(item["benchmark_return_pct"], minimum=-100)
        _number(item["max_drawdown_pct"], minimum=0, maximum=100)
        _number(item["win_rate_pct"], minimum=0, maximum=100)
        _number(item["total_fees"], minimum=0)
        _number(item["fee_bps_per_side"], minimum=0)
        _number(item["slippage_bps_per_side"], minimum=0)
        _nonnegative_int(item["number_of_trades"])

    overlap_pairs = sum(
        start_a < end_b and start_b < end_a
        for i, (start_a, end_a) in enumerate(windows)
        for start_b, end_b in windows[i + 1 :]
    )
    distinct_windows = len(set(windows))
    cost_pairs = {
        (item["fee_bps_per_side"], item["slippage_bps_per_side"])
        for item in observations
    }
    candidates = {item["selected_candidate"] for item in observations}
    returns = [float(item["net_return_pct"]) for item in observations]
    drawdowns = [float(item["max_drawdown_pct"]) for item in observations]

    warnings: list[str] = []
    if len(observations) < 2:
        warnings.append("Fewer than two saved observations; no repeatability evidence.")
    if overlap_pairs:
        warnings.append("Test windows overlap; observations are not independent periods.")
    if distinct_windows < len(windows):
        warnings.append("At least one test window appears more than once; repeated windows count as separate records.")
    if len(cost_pairs) > 1:
        warnings.append("Fee or slippage assumptions differ between saved records.")
    if len(candidates) > 1:
        warnings.append("Selected candidates differ; this is not a fixed-strategy track record.")
    if len(set(durations)) > 1:
        warnings.append("Test durations differ; raw returns are not duration-normalized.")
    if any(duration != 7 for duration in durations):
        warnings.append("Not all observations cover seven days; positive counts are per record, not per week.")
    warnings.append("The results are descriptive only; no compounded return, portfolio drawdown, statistical significance, or future profit is inferred.")

    all_weekly_nonoverlapping = (
        len(observations) >= 2
        and all(duration == 7 for duration in durations)
        and not overlap_pairs
    )
    return {
        "statistics_version": STATISTICS_VERSION,
        "protocol_version": SUPPORTED_PROTOCOL,
        "aggregation_type": "descriptive_saved_observations",
        "selection": {
            "method": "latest_by_created_at",
            "limit": limit,
            "record_ids": [item["record_id"] for item in observations],
        },
        "sample_size": len(observations),
        "status": "descriptive_only" if len(observations) >= 2 else "insufficient_records",
        "profitable_observation_count": sum(value > 0 for value in returns),
        "losing_observation_count": sum(value < 0 for value in returns),
        "flat_observation_count": sum(value == 0 for value in returns),
        "return_distribution_pct": _distribution(returns),
        "individual_max_drawdown_distribution_pct": _distribution(drawdowns),
        "distinct_test_windows": distinct_windows,
        "overlapping_test_window_pairs": overlap_pairs,
        "repeated_test_window_count": len(windows) - distinct_windows,
        "different_cost_assumptions": len(cost_pairs) > 1,
        "different_selected_candidates": len(candidates) > 1,
        "all_test_windows_seven_days": all(duration == 7 for duration in durations),
        "nonoverlapping_seven_day_windows": all_weekly_nonoverlapping,
        "warnings": warnings,
        "observations": observations,
        "automatic_winner_selection": False,
        "strategy_promotion_allowed": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
    }
