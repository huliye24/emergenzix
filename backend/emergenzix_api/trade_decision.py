"""Pure PAPER strategy-position-portfolio decision module (domains 4-6).

The module converts an already-reviewed signal into an auditable trade plan.
It cannot create orders and grants no PAPER, TESTNET, or LIVE permission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .position_sizing import (
    LongPaperSizingRequest,
    PositionSizingDecision,
    size_long_paper_position,
)


DecisionStatus = Literal["approved_for_review", "rejected"]


@dataclass(frozen=True, slots=True)
class TradeDecisionRequest:
    """Frozen inputs from signal evidence, strategy rules, and portfolio state."""

    strategy_version_id: str
    signal_id: str
    equity: float
    available_cash: float
    entry_price: float
    protective_stop: float
    conservative_win_probability: float
    net_payoff_ratio: float
    maximum_trade_risk_fraction: float
    fractional_kelly: float
    existing_portfolio_stop_risk: float
    maximum_portfolio_risk_fraction: float
    existing_cluster_stop_risk: float
    maximum_cluster_risk_fraction: float
    maximum_notional: float
    fee_bps: float = 0.0
    slippage_bps: float = 0.0


@dataclass(frozen=True, slots=True)
class TradeDecision:
    status: DecisionStatus
    reason: str
    robust_expectancy_r: float
    full_kelly_fraction: float
    approved_risk_fraction: float
    portfolio_risk_before: float
    portfolio_risk_after: float
    cluster_risk_before: float
    cluster_risk_after: float
    sizing: PositionSizingDecision
    creates_order: bool = False


def _reject(reason: str, *, expectancy: float = 0.0, kelly: float = 0.0) -> TradeDecision:
    sizing = size_long_paper_position(
        LongPaperSizingRequest(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    )
    return TradeDecision(
        "rejected", reason, expectancy, kelly, 0.0, 0.0, 0.0, 0.0, 0.0, sizing
    )


def plan_long_paper_trade(request: TradeDecisionRequest) -> TradeDecision:
    """Plan one long PAPER trade using edge, Kelly, and aggregate stop risk.

    ``conservative_win_probability`` must be an out-of-sample lower estimate,
    not an in-sample score. ``net_payoff_ratio`` must already include the
    strategy's expected trading costs. Portfolio and correlation-cluster risk
    are measured as currency loss at protective stops.
    """

    numeric = (
        request.equity,
        request.available_cash,
        request.entry_price,
        request.protective_stop,
        request.conservative_win_probability,
        request.net_payoff_ratio,
        request.maximum_trade_risk_fraction,
        request.fractional_kelly,
        request.existing_portfolio_stop_risk,
        request.maximum_portfolio_risk_fraction,
        request.existing_cluster_stop_risk,
        request.maximum_cluster_risk_fraction,
        request.maximum_notional,
        request.fee_bps,
        request.slippage_bps,
    )
    if not request.strategy_version_id.strip() or not request.signal_id.strip():
        return _reject("strategy_version_id and signal_id are required")
    if any(isinstance(value, bool) or not math.isfinite(value) for value in numeric):
        return _reject("all numeric inputs must be finite")
    if request.equity <= 0:
        return _reject("equity must be positive")
    if not 0 <= request.conservative_win_probability <= 1:
        return _reject("conservative win probability must be in [0, 1]")
    if request.net_payoff_ratio <= 0:
        return _reject("net payoff ratio must be positive")
    if not 0 < request.maximum_trade_risk_fraction <= 0.1:
        return _reject("maximum trade risk fraction must be in (0, 0.1]")
    if not 0 < request.fractional_kelly <= 0.5:
        return _reject("fractional Kelly must be in (0, 0.5]")
    if request.existing_portfolio_stop_risk < 0 or request.existing_cluster_stop_risk < 0:
        return _reject("existing stop risk cannot be negative")
    if not 0 < request.maximum_cluster_risk_fraction <= request.maximum_portfolio_risk_fraction <= 1:
        return _reject("cluster risk cap must be positive and no larger than portfolio cap")

    probability = request.conservative_win_probability
    payoff = request.net_payoff_ratio
    expectancy = probability * payoff - (1.0 - probability)
    full_kelly = max(0.0, expectancy / payoff)
    if expectancy <= 0:
        return _reject("non_positive_robust_expectancy", expectancy=expectancy, kelly=full_kelly)

    portfolio_cap = request.equity * request.maximum_portfolio_risk_fraction
    cluster_cap = request.equity * request.maximum_cluster_risk_fraction
    portfolio_headroom = max(0.0, portfolio_cap - request.existing_portfolio_stop_risk)
    cluster_headroom = max(0.0, cluster_cap - request.existing_cluster_stop_risk)
    risk_budget = min(
        request.equity * request.maximum_trade_risk_fraction,
        request.equity * full_kelly * request.fractional_kelly,
        portfolio_headroom,
        cluster_headroom,
    )
    if risk_budget <= 0:
        return _reject("no_portfolio_risk_headroom", expectancy=expectancy, kelly=full_kelly)

    approved_fraction = risk_budget / request.equity
    sizing = size_long_paper_position(
        LongPaperSizingRequest(
            equity=request.equity,
            available_cash=request.available_cash,
            entry_price=request.entry_price,
            protective_stop=request.protective_stop,
            risk_fraction=approved_fraction,
            max_notional=request.maximum_notional,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
        )
    )
    if not sizing.executable:
        return _reject(
            f"position_sizing_rejected:{sizing.rejection_reason}",
            expectancy=expectancy,
            kelly=full_kelly,
        )

    accepted_risk = sizing.estimated_loss_at_stop
    return TradeDecision(
        status="approved_for_review",
        reason="positive_edge_within_trade_and_portfolio_limits",
        robust_expectancy_r=expectancy,
        full_kelly_fraction=full_kelly,
        approved_risk_fraction=accepted_risk / request.equity,
        portfolio_risk_before=request.existing_portfolio_stop_risk,
        portfolio_risk_after=request.existing_portfolio_stop_risk + accepted_risk,
        cluster_risk_before=request.existing_cluster_stop_risk,
        cluster_risk_after=request.existing_cluster_stop_risk + accepted_risk,
        sizing=sizing,
    )
