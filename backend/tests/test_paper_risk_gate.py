from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.paper_risk_gate import PaperRiskInputs, evaluate_paper_risk


NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def inputs(**overrides):
    values = {
        "now": NOW,
        "market_data_at": NOW - timedelta(seconds=5),
        "balance_at": NOW - timedelta(seconds=5),
        "max_market_age_seconds": 60.0,
        "max_balance_age_seconds": 60.0,
        "balance_known": True,
        "account_frozen": False,
        "uncertain_order_count": 0,
        "unprotected_position_count": 0,
    }
    values.update(overrides)
    return PaperRiskInputs(**values)


def test_fresh_known_paper_state_allows_new_orders():
    result = evaluate_paper_risk(inputs())
    assert result.allow_new_orders is True
    assert result.reasons == ()


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"market_data_at": NOW - timedelta(seconds=61)}, "market_data_stale"),
        ({"balance_known": False}, "balance_unknown"),
        ({"balance_at": None}, "balance_missing"),
        ({"uncertain_order_count": 1}, "uncertain_order_state"),
        ({"account_frozen": True}, "account_frozen"),
        ({"unprotected_position_count": 1}, "protective_stop_missing"),
        ({"execution_mode": "LIVE"}, "execution_mode_not_paper"),
    ],
)
def test_unsafe_state_blocks_new_orders_but_preserves_emergency_actions(override, reason):
    result = evaluate_paper_risk(inputs(**override))
    assert result.allow_new_orders is False
    assert reason in result.reasons
    assert result.allow_cancel_orders is True
    assert result.allow_reduce_risk is True


def test_future_and_naive_timestamps_fail_closed():
    future = evaluate_paper_risk(inputs(market_data_at=NOW + timedelta(seconds=1)))
    naive = evaluate_paper_risk(inputs(balance_at=NOW.replace(tzinfo=None)))
    assert "market_data_from_future" in future.reasons
    assert "balance_not_timezone_aware" in naive.reasons
