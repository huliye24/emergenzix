"""Unified risk-budget pyramiding tests with no order side effects."""

from __future__ import annotations

import pytest

from emergenzix_api.pyramiding_decision import PyramidPlanRequest, plan_long_pyramid_add


def request(**overrides: object) -> PyramidPlanRequest:
    values = dict(
        strategy_version_id="breakout-v1",
        signal_id="btc-4h-add-1",
        equity=10_000.0,
        available_cash=8_000.0,
        current_price=110.0,
        average_entry=100.0,
        initial_entry=100.0,
        initial_stop=95.0,
        protective_stop=102.0,
        current_quantity=10.0,
        initial_notional=1_000.0,
        additions_completed=0,
        same_direction_confirmed=True,
        conservative_win_probability=0.55,
        net_payoff_ratio=2.0,
        maximum_trade_risk_fraction=0.01,
        fractional_kelly=0.20,
        existing_portfolio_stop_risk=100.0,
        maximum_portfolio_risk_fraction=0.05,
        existing_cluster_stop_risk=50.0,
        maximum_cluster_risk_fraction=0.03,
        maximum_total_notional=3_000.0,
        fee_bps=10.0,
        slippage_bps=20.0,
    )
    values.update(overrides)
    return PyramidPlanRequest(**values)  # type: ignore[arg-type]


def test_profitable_locked_confirmed_position_gets_reviewable_add() -> None:
    decision = plan_long_pyramid_add(request())

    assert decision.status == "approved_for_review"
    assert decision.add_index == 0
    assert decision.r_multiple == pytest.approx(2.0)
    assert decision.locked_profit == pytest.approx(20.0)
    assert 0 < decision.approved_add_notional <= 500.0
    assert decision.portfolio_risk_after <= 500.0
    assert decision.cluster_risk_after <= 300.0
    assert decision.creates_order is False


def test_second_add_is_smaller_than_first() -> None:
    first = plan_long_pyramid_add(request(additions_completed=0))
    second = plan_long_pyramid_add(request(additions_completed=1))

    assert first.requested_add_notional == pytest.approx(500.0)
    assert second.requested_add_notional == pytest.approx(250.0)
    assert second.approved_add_notional < first.approved_add_notional


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"current_price": 99.0}, "position_not_profitable"),
        ({"protective_stop": 99.0}, "profit_not_locked_by_stop"),
        ({"same_direction_confirmed": False}, "same_direction_signal_not_confirmed"),
        ({"current_price": 104.0}, "next_r_threshold_not_reached"),
        ({"conservative_win_probability": 0.30}, "non_positive_robust_expectancy"),
        ({"existing_portfolio_stop_risk": 500.0}, "no_risk_headroom"),
        ({"additions_completed": 2}, "maximum_additions_reached"),
    ],
)
def test_addition_fails_closed_when_any_gate_fails(
    overrides: dict[str, object], reason: str
) -> None:
    decision = plan_long_pyramid_add(request(**overrides))
    assert decision.status == "rejected"
    assert decision.reason == reason
    assert decision.approved_add_notional == 0.0


def test_cluster_headroom_caps_incremental_stop_risk() -> None:
    decision = plan_long_pyramid_add(request(existing_cluster_stop_risk=295.0))
    assert decision.status == "approved_for_review"
    assert decision.sizing.estimated_loss_at_stop == pytest.approx(5.0)
    assert decision.cluster_risk_after == pytest.approx(300.0)


def test_current_stop_is_never_modified_by_planner() -> None:
    decision = plan_long_pyramid_add(request(protective_stop=104.0))
    assert decision.status == "approved_for_review"
    assert decision.protective_stop == 104.0
