"""Causal UTC weekly UP-UP entry permission for offline research only.

Input candles are 4-hour UTC OHLCV bars whose timestamps mark bar *opens*.
This module never places trades and does not modify exit rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Sequence

from .schemas import Bar

FOUR_HOURS = timedelta(hours=4)
WEEK = timedelta(days=7)
BARS_PER_WEEK = 42


def _week_start(ts: datetime) -> datetime:
    return (ts - timedelta(days=ts.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def completed_week_closes(bars: Sequence[Bar], *, as_of: datetime) -> list[tuple[datetime, float]]:
    """Return only full, gapless Monday-Sunday weeks observed by as_of (UTC).

    as_of is an exclusive upper bound on knowledge: a candle is only available
    after its full four-hour interval has closed. Later bars are ignored.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    closes: list[tuple[datetime, float]] = []
    current: list[tuple[datetime, float]] = []
    current_week: datetime | None = None
    previous: datetime | None = None
    for bar in bars:
        ts = bar.timestamp.astimezone(timezone.utc)
        if ts.minute or ts.second or ts.microsecond or ts.hour % 4:
            raise ValueError("candles must align to UTC 4-hour boundaries")
        if previous is not None and ts <= previous:
            raise ValueError("candles must be strictly increasing without duplicates")
        if not isfinite(bar.close) or bar.close <= 0:
            raise ValueError("candles must have finite positive closes")
        previous = ts
        if ts + FOUR_HOURS > as_of:
            continue
        week = _week_start(ts)
        if current_week is not None and week != current_week:
            if (
                len(current) == BARS_PER_WEEK
                and current[0][0] == current_week
                and current[-1][0] + FOUR_HOURS == current_week + WEEK
                and all(b[0] - a[0] == FOUR_HOURS for a, b in zip(current, current[1:]))
            ):
                closes.append((current_week + WEEK, current[-1][1]))
            current = []
        current_week = week
        current.append((ts, bar.close))
    if current_week is not None and (
        len(current) == BARS_PER_WEEK
        and current[0][0] == current_week
        and current[-1][0] + FOUR_HOURS == current_week + WEEK
        and all(b[0] - a[0] == FOUR_HOURS for a, b in zip(current, current[1:]))
    ):
        closes.append((current_week + WEEK, current[-1][1]))
    return closes


def up_up_entry_allowed(bars: Sequence[Bar], *, as_of: datetime) -> bool:
    """Require two consecutive positive trailing-four-week states.

    A state is computed from close[i] / close[i-4] - 1 >= 0.
    Six consecutive, complete weekly closes are needed for two states.
    A missing or incomplete week breaks the chain (fail closed).
    This is an experimental definition, NOT an established optimal threshold.
    """
    weeks = completed_week_closes(bars, as_of=as_of)
    if len(weeks) < 6:
        return False
    recent = weeks[-6:]
    if any(b[0] - a[0] != WEEK for a, b in zip(recent, recent[1:])):
        return False
    return recent[4][1] >= recent[0][1] and recent[5][1] >= recent[1][1]


def permit_signal(bars: Sequence[Bar], *, as_of: datetime, signal: str) -> bool:
    """Gate only new long entries; exits always pass through unchanged."""
    if signal == "exit_long":
        return True
    if signal != "enter_long":
        raise ValueError("unknown signal")
    return up_up_entry_allowed(bars, as_of=as_of)
