"""Guardrails for the capital/period one-command historical research interface."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from emergenzix_api.date_range_backtest_api import DateRangeBacktestCreate
from emergenzix_api.quick_backtest import (
    MAX_TEST_DAYS,
    parse_quick_period,
    resolve_quick_backtest,
)


@pytest.mark.parametrize(
    ("period", "days"),
    [("1w", 7), ("1m", 30), ("1y", 365), ("3y", 1095),
     ("14d", 14), ("1095d", 1095)],
)
def test_period_aliases_and_bounded_days(period: str, days: int) -> None:
    assert parse_quick_period(period) == days


@pytest.mark.parametrize(
    "period",
    ["", "0d", "1096d", "1w2d", "1 month", "2y", "1M", "-7d",
     "0.5w", "0007d", "7", "../secret"],
)
def test_unrecognized_or_unbounded_period_fails_closed(period: str) -> None:
    with pytest.raises(ValueError, match="period"):
        parse_quick_period(period)


def test_two_inputs_select_last_completed_utc_window() -> None:
    start, days = resolve_quick_backtest(
        initial_cash=100.0, period="1w", utc_today=date(2026, 10, 26),
    )
    assert start == date(2026, 10, 19)
    assert days == 7
    assert start + timedelta(days=days) == date(2026, 10, 26)


def test_explicit_start_freezes_historical_replay() -> None:
    assert resolve_quick_backtest(
        initial_cash=120, period="1m",
        start_date=date(2025, 10, 21), utc_today=date(2026, 10, 26),
    ) == (date(2025, 10, 21), 30)


@pytest.mark.parametrize("capital", [0, -1, float("nan"), float("inf"),
                                      1_000_000_001, True, "100"])
def test_capital_must_be_positive_finite_and_not_boolean(capital) -> None:
    with pytest.raises(ValueError, match="initial_cash"):
        resolve_quick_backtest(
            initial_cash=capital, period="1w", utc_today=date(2026, 10, 26),
        )


def test_never_request_future_or_incomplete_candles() -> None:
    with pytest.raises(ValueError, match="ended"):
        resolve_quick_backtest(
            initial_cash=100, period="1w",
            start_date=date(2026, 10, 21), utc_today=date(2026, 10, 26),
        )


def test_existing_date_range_api_accepts_three_year_holdout_but_not_more() -> None:
    valid = DateRangeBacktestCreate(
        start_date=date(2021, 1, 1),
        duration_days=MAX_TEST_DAYS,
        initial_cash=100,
    )
    assert valid.duration_days == 1095
    with pytest.raises(ValidationError):
        DateRangeBacktestCreate(
            start_date=date(2021, 1, 1), duration_days=1096,
        )


def test_quick_cli_forwards_exact_parameters_without_running_backtest(monkeypatch) -> None:
    import emergenzix_api.quick_backtest_cli as cli

    captured: list[str] = []

    def fake_legacy(argv):
        captured.extend(argv)
        return 0

    monkeypatch.setattr(cli, "run_legacy_date_range", fake_legacy)
    status = cli.main([
        "--capital", "120", "--period", "1y", "--start", "2022-01-01",
        "--db-path", "research.sqlite3", "--output", "report.json",
    ])
    assert status == 0
    assert captured == [
        "--start-date", "2022-01-01", "--duration-days", "365",
        "--initial-cash", "120.0", "--output", "report.json",
        "--db-path", "research.sqlite3",
    ]


def test_quick_cli_invalid_period_never_calls_runner(monkeypatch) -> None:
    import emergenzix_api.quick_backtest_cli as cli

    monkeypatch.setattr(
        cli, "run_legacy_date_range",
        lambda _args: pytest.fail("invalid period must never run"),
    )
    assert cli.main(["--capital", "100", "--period", "1096d"]) == 2


def test_quick_api_reuses_existing_immutable_paper_only_service(client, monkeypatch) -> None:
    import emergenzix_api.date_range_backtest_api as api

    captured = []

    def fake_create(_db, **kwargs):
        captured.append(kwargs)
        return ({
            "id": "a" * 64, "safety": {
                "orders_created": False, "live_trading_enabled": False,
            },
            "configuration": kwargs,
        }, True)

    monkeypatch.setattr(api, "create_date_range_backtest", fake_create)
    response = client.post(
        "/api/v1/research/date-range-backtests/quick",
        json={
            "initial_cash": 100, "period": "1m", "start_date": "2025-10-21",
        },
    )
    assert response.status_code == 201, response.text
    assert captured[0]["start_date"] == date(2025, 10, 21)
    assert captured[0]["duration_days"] == 30
    assert captured[0]["initial_cash"] == 100
    assert captured[0]["training_days"] == 90
    assert response.json()["safety"]["orders_created"] is False

    invalid = client.post(
        "/api/v1/research/date-range-backtests/quick",
        json={"initial_cash": 100, "period": "1096d"},
    )
    assert invalid.status_code == 422
    assert len(captured) == 1
