"""Domains 4-6 decision tests; no API, database, or order side effects."""

from __future__ import annotations

import pytest

from emergenzix_api.trade_decision import TradeDecisionRequest, plan_long_paper_trade


def request(**overrides: object) -> TradeDecisionRequest:
    values = dict(
        strategy_version_id="breakout-v1",
        signal_id="btc-4h-20261030T0800Z",
        equity=10_000.0,
        available_cash=10_000.0,
        entry_price=100.0,
        protective_stop=95.0,
        conservative_win_probability=0.55,
        net_payoff_ratio=2.0,
        maximum_trade_risk_fraction=0.01,
        fractional_kelly=0.20,
        existing_portfolio_stop_risk=100.0,
        maximum_portfolio_risk_fraction=0.05,
        existing_cluster_stop_risk=50.0,
        maximum_cluster_risk_fraction=0.03,
        maximum_notional=5_000.0,
        fee_bps=10.0,
        slippage_bps=20.0,
    )
    values.update(overrides)
    return TradeDecisionRequest(**values)  # type: ignore[arg-type]


def test_positive_edge_produces_reviewable_plan_not_order() -> None:
    decision = plan_long_paper_trade(request())

    assert decision.status == "approved_for_review"
    assert decision.robust_expectancy_r == pytest.approx(0.65)
    assert decision.sizing.executable
    assert decision.sizing.estimated_loss_at_stop <= 100.0
    assert decision.portfolio_risk_after <= 500.0
    assert decision.cluster_risk_after <= 300.0
    assert decision.creates_order is False


def test_non_positive_conservative_edge_is_rejected() -> None:
    decision = plan_long_paper_trade(
        request(conservative_win_probability=0.30, net_payoff_ratio=2.0)
    )

    assert decision.status == "rejected"
    assert decision.reason == "non_positive_robust_expectancy"
    assert decision.sizing.executable is False


def test_cluster_headroom_caps_trade_risk() -> None:
    decision = plan_long_paper_trade(
        request(existing_cluster_stop_risk=290.0)
    )

    assert decision.status == "approved_for_review"
    assert decision.sizing.estimated_loss_at_stop == pytest.approx(10.0)
    assert decision.cluster_risk_after == pytest.approx(300.0)


def test_exhausted_portfolio_risk_rejects_trade() -> None:
    decision = plan_long_paper_trade(
        request(existing_portfolio_stop_risk=500.0)
    )

    assert decision.status == "rejected"
    assert decision.reason == "no_portfolio_risk_headroom"


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy_version_id": ""},
        {"conservative_win_probability": 1.1},
        {"fractional_kelly": 0.0},
        {"existing_cluster_stop_risk": -1.0},
        {"maximum_cluster_risk_fraction": 0.06},
    ],
)
def test_invalid_or_unsafe_inputs_fail_closed(overrides: dict[str, object]) -> None:
    decision = plan_long_paper_trade(request(**overrides))
    assert decision.status == "rejected"
    assert decision.creates_order is False
