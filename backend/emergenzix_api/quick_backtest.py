"""Normalize small-capital backtest requests; never fetch data or place orders.

A quick request only asks for initial capital and a period. An optional date
makes the same request reproducible for a historical interval. Fixed periods
are measured in UTC days: 1m = 30 days, 1y = 365 days, 3y = 1095 days.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone


MAX_TEST_DAYS = 1095
QUICK_PERIOD_DAYS = {"1w": 7, "1m": 30, "1y": 365, "3y": 1095}


def parse_quick_period(value: str) -> int:
    """Parse a fixed-day duration from an explicit, bounded notation."""
    if not isinstance(value, str):
        raise ValueError("period must be a string such as 1w, 1m, 1y or 3y")
    if value in QUICK_PERIOD_DAYS:
        return QUICK_PERIOD_DAYS[value]
    if not re.fullmatch(r"[1-9][0-9]{0,3}d", value):
        raise ValueError("period must be 1w, 1m, 1y, 3y or an integer Nd")
    days = int(value[:-1])
    if not 1 <= days <= MAX_TEST_DAYS:
        raise ValueError(f"period must be between 1 and {MAX_TEST_DAYS} days")
    return days


def resolve_quick_backtest(
    *, initial_cash: float, period: str, start_date: date | None = None,
    utc_today: date | None = None,
) -> tuple[date, int]:
    """Return a fully historical [start, end) UTC date window.

    If start_date is absent, the last possible end boundary is today's
    00:00 UTC, excluding the current unfinished day. Archive availability
    is enforced separately by the verified public-data fetcher.
    """
    if isinstance(initial_cash, bool) or not isinstance(initial_cash, (int, float)):
        raise ValueError("initial_cash must be a finite positive number")
    if not math.isfinite(initial_cash) or not 0 < initial_cash <= 1_000_000_000:
        raise ValueError("initial_cash must be between 0 and 1,000,000,000")
    days = parse_quick_period(period)
    today = utc_today if utc_today is not None else datetime.now(timezone.utc).date()
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("utc_today must be a calendar date")
    if start_date is not None and (
        not isinstance(start_date, date) or isinstance(start_date, datetime)
    ):
        raise ValueError("start_date must be a calendar date")
    start = start_date if start_date is not None else today - timedelta(days=days)
    if start + timedelta(days=days) > today:
        raise ValueError("the requested test period must have ended before today (UTC)")
    return start, days
