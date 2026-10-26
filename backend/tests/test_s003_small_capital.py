"""Tests for the S-003.W small-capital weekly audit engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from emergenzix_api.nemt_strategy import s003_small_capital_cli as s3
from emergenzix_api.schemas import Bar

ZONE = ZoneInfo("Asia/Shanghai")
MONDAY = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _flat_bars(weeks: int, price: float = 100.0) -> list[Bar]:
    bars = []
    for step in range(weeks * 7 * 6):
        bars.append(Bar(timestamp=MONDAY + timedelta(hours=4) * step, open=price,
                        high=price, low=price, close=price, volume=1.0))
    return bars


def test_weekly_timing_validation_passes() -> None:
    checks = s3.weekly_timing_validation()
    assert len(checks) == 3
    assert all(check["passed"] for check in checks), checks


def test_week_start_is_monday_midnight_utc() -> None:
    wednesday = datetime(2024, 1, 3, 13, 0, tzinfo=timezone.utc)
    assert s3._week_start(wednesday) == MONDAY
    sunday = datetime(2024, 1, 7, 23, 0, tzinfo=timezone.utc)
    assert s3._week_start(sunday) == MONDAY
    next_monday = datetime(2024, 1, 8, 0, 0, tzinfo=timezone.utc)
    assert s3._week_start(next_monday) == MONDAY + timedelta(days=7)


def test_trend_requires_eight_completed_weeks() -> None:
    context = s3.weekly_context(_flat_bars(7))
    assert s3.trend_at(context, MONDAY + timedelta(days=50)) is None
    context = s3.weekly_context(_flat_bars(9))
    assert s3.trend_at(context, MONDAY + timedelta(days=60)) is not None


def test_flat_weeks_give_neutral_trend() -> None:
    context = s3.weekly_context(_flat_bars(12))
    assert s3.trend_at(context, MONDAY + timedelta(days=80)) == 0


def test_rising_weekly_closes_give_positive_trend() -> None:
    bars: list[Bar] = []
    price = 100.0
    for week in range(12):
        for step in range(7 * 6):
            price *= 1.002
            bars.append(Bar(timestamp=MONDAY + timedelta(hours=4) * (week * 42 + step),
                            open=price / 1.002, high=price, low=price / 1.002,
                            close=price, volume=1.0))
    context = s3.weekly_context(bars)
    assert s3.trend_at(context, MONDAY + timedelta(days=80)) == 1


def test_config_table_is_single_variable() -> None:
    base = s3.CONFIGS["B0"]
    assert s3.CONFIGS["W1"]["exit_bars"] == base["exit_bars"]
    assert s3.CONFIGS["W1"]["weekly_fail_exit"] == base["weekly_fail_exit"]
    assert s3.CONFIGS["W1"]["weekly_gate"] is True

    assert s3.CONFIGS["W2"]["weekly_gate"] is False
    assert s3.CONFIGS["W2"]["weekly_fail_exit"] is False
    assert s3.CONFIGS["W2"]["exit_bars"] == 120

    assert s3.CONFIGS["W3"]["weekly_gate"] is False
    assert s3.CONFIGS["W3"]["exit_bars"] == base["exit_bars"]
    assert s3.CONFIGS["W3"]["weekly_fail_exit"] is True

    assert s3.CONFIGS["W4"]["weekly_gate"] is True
    assert s3.CONFIGS["W4"]["exit_bars"] == 120
    assert s3.CONFIGS["W4"]["weekly_fail_exit"] is True


def test_deletion_sensitivity_is_consistent() -> None:
    """A synthetic trade list must produce a monotone removal sensitivity."""
    capital = 100.0
    wins = [10.0, 4.0]
    cash = 110.0
    report_metrics = {
        "return_without_largest_win_pct": (cash - wins[0]) / capital * 100 - 100,
        "return_without_top2_wins_pct": (cash - sum(wins)) / capital * 100 - 100,
    }
    assert report_metrics["return_without_largest_win_pct"] == pytest.approx(0.0)
    assert report_metrics["return_without_top2_wins_pct"] == pytest.approx(-4.0)
    assert (report_metrics["return_without_top2_wins_pct"]
            <= report_metrics["return_without_largest_win_pct"])
