"""Pure risk-budgeted pyramiding plans for PAPER review (domains 4-6).

This module plans decreasing additions to an existing profitable long position.
It has no persistence, exchange adapter, or order creation capability.
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


PyramidStatus = Literal["approved_for_review", "rejected"]


@dataclass(frozen=True, slots=True)
class PyramidPlanRequest:
    strategy_version_id: str
    signal_id: str
    equity: float
    available_cash: float
    current_price: float
    average_entry: float
    initial_entry: float
    initial_stop: float
    protective_stop: float
    current_quantity: float
    initial_notional: float
    additions_completed: int
    same_direction_confirmed: bool
    conservative_win_probability: float
    net_payoff_ratio: float
    maximum_trade_risk_fraction: float
    fractional_kelly: float
    existing_portfolio_stop_risk: float
    maximum_portfolio_risk_fraction: float
    existing_cluster_stop_risk: float
    maximum_cluster_risk_fraction: float
    maximum_total_notional: float
    add_at_r: tuple[float, ...] = (1.0, 2.0)
    add_size_fractions: tuple[float, ...] = (0.5, 0.25)
    fee_bps: float = 0.0
    slippage_bps: float = 0.0


@dataclass(frozen=True, slots=True)
class PyramidPlan:
    status: PyramidStatus
    reason: str
    add_index: int | None
    r_multiple: float
    locked_profit: float
    current_open_stop_risk: float
    robust_expectancy_r: float
    full_kelly_fraction: float
    requested_add_notional: float
    approved_add_notional: float
    portfolio_risk_after: float
    cluster_risk_after: float
    protective_stop: float
    sizing: PositionSizingDecision
    creates_order: bool = False


def _empty_sizing() -> PositionSizingDecision:
    return size_long_paper_position(LongPaperSizingRequest(0, 0, 0, 0, 0, 0))


def _reject(
    reason: str,
    *,
    request: PyramidPlanRequest | None = None,
    r_multiple: float = 0.0,
    locked_profit: float = 0.0,
    open_risk: float = 0.0,
    expectancy: float = 0.0,
    kelly: float = 0.0,
) -> PyramidPlan:
    return PyramidPlan(
        status="rejected",
        reason=reason,
        add_index=None,
        r_multiple=r_multiple,
        locked_profit=locked_profit,
        current_open_stop_risk=open_risk,
        robust_expectancy_r=expectancy,
        full_kelly_fraction=kelly,
        requested_add_notional=0.0,
        approved_add_notional=0.0,
        portfolio_risk_after=request.existing_portfolio_stop_risk if request else 0.0,
        cluster_risk_after=request.existing_cluster_stop_risk if request else 0.0,
        protective_stop=request.protective_stop if request else 0.0,
        sizing=_empty_sizing(),
    )


def plan_long_pyramid_add(request: PyramidPlanRequest) -> PyramidPlan:
    """Return a reviewable decreasing add plan or a fail-closed rejection."""

    numeric = (
        request.equity, request.available_cash, request.current_price,
        request.average_entry, request.initial_entry, request.initial_stop,
        request.protective_stop, request.current_quantity, request.initial_notional,
        request.conservative_win_probability, request.net_payoff_ratio,
        request.maximum_trade_risk_fraction, request.fractional_kelly,
        request.existing_portfolio_stop_risk, request.maximum_portfolio_risk_fraction,
        request.existing_cluster_stop_risk, request.maximum_cluster_risk_fraction,
        request.maximum_total_notional, request.fee_bps, request.slippage_bps,
        *request.add_at_r, *request.add_size_fractions,
    )
    if not request.strategy_version_id.strip() or not request.signal_id.strip():
        return _reject("strategy_version_id and signal_id are required", request=request)
    if any(isinstance(value, bool) or not math.isfinite(value) for value in numeric):
        return _reject("all numeric inputs must be finite", request=request)
    if request.equity <= 0 or request.available_cash <= 0:
        return _reject("equity and available cash must be positive", request=request)
    if min(request.current_price, request.average_entry, request.initial_entry,
           request.initial_stop, request.protective_stop) <= 0:
        return _reject("prices must be positive", request=request)
    if request.initial_stop >= request.initial_entry:
        return _reject("initial long stop must be below initial entry", request=request)
    if request.current_quantity <= 0 or request.initial_notional <= 0:
        return _reject("an existing positive position is required", request=request)
    if request.additions_completed < 0 or isinstance(request.additions_completed, bool):
        return _reject("additions_completed must be a non-negative integer", request=request)
    if len(request.add_at_r) != len(request.add_size_fractions) or not request.add_at_r:
        return _reject("add thresholds and fractions must align", request=request)
    if tuple(sorted(request.add_at_r)) != request.add_at_r or any(x <= 0 for x in request.add_at_r):
        return _reject("add thresholds must be positive and increasing", request=request)
    if any(not 0 < x < 1 for x in request.add_size_fractions):
        return _reject("add fractions must be in (0, 1)", request=request)
    if any(a <= b for a, b in zip(request.add_size_fractions, request.add_size_fractions[1:])):
        return _reject("each add fraction must be smaller than the previous", request=request)
    if not 0 <= request.conservative_win_probability <= 1 or request.net_payoff_ratio <= 0:
        return _reject("probability and payoff inputs are invalid", request=request)
    if not 0 < request.maximum_trade_risk_fraction <= 0.1:
        return _reject("maximum trade risk fraction must be in (0, 0.1]", request=request)
    if not 0 < request.fractional_kelly <= 0.5:
        return _reject("fractional Kelly must be in (0, 0.5]", request=request)
    if request.existing_portfolio_stop_risk < 0 or request.existing_cluster_stop_risk < 0:
        return _reject("existing stop risk cannot be negative", request=request)
    if not 0 < request.maximum_cluster_risk_fraction <= request.maximum_portfolio_risk_fraction <= 1:
        return _reject("cluster cap must be no larger than portfolio cap", request=request)
    if request.maximum_total_notional <= 0:
        return _reject("maximum total notional must be positive", request=request)

    initial_risk_distance = request.initial_entry - request.initial_stop
    r_multiple = (request.current_price - request.initial_entry) / initial_risk_distance
    locked_per_unit = max(0.0, request.protective_stop - request.average_entry)
    locked_profit = locked_per_unit * request.current_quantity
    open_risk_per_unit = max(0.0, request.average_entry - request.protective_stop)
    open_risk = open_risk_per_unit * request.current_quantity

    context = dict(request=request, r_multiple=r_multiple,
                   locked_profit=locked_profit, open_risk=open_risk)
    if request.current_price <= request.average_entry:
        return _reject("position_not_profitable", **context)
    if request.protective_stop <= request.average_entry or locked_profit <= 0:
        return _reject("profit_not_locked_by_stop", **context)
    if request.protective_stop >= request.current_price:
        return _reject("protective_stop_must_remain_below_current_price", **context)
    if not request.same_direction_confirmed:
        return _reject("same_direction_signal_not_confirmed", **context)
    if request.additions_completed >= len(request.add_at_r):
        return _reject("maximum_additions_reached", **context)

    add_index = request.additions_completed
    if r_multiple < request.add_at_r[add_index]:
        return _reject("next_r_threshold_not_reached", **context)

    probability, payoff = request.conservative_win_probability, request.net_payoff_ratio
    expectancy = probability * payoff - (1.0 - probability)
    full_kelly = max(0.0, expectancy / payoff)
    context.update(expectancy=expectancy, kelly=full_kelly)
    if expectancy <= 0:
        return _reject("non_positive_robust_expectancy", **context)

    portfolio_headroom = max(
        0.0,
        request.equity * request.maximum_portfolio_risk_fraction
        - request.existing_portfolio_stop_risk,
    )
    cluster_headroom = max(
        0.0,
        request.equity * request.maximum_cluster_risk_fraction
        - request.existing_cluster_stop_risk,
    )
    trade_headroom = max(
        0.0,
        request.equity * request.maximum_trade_risk_fraction
        + locked_profit - open_risk,
    )
    kelly_budget = request.equity * full_kelly * request.fractional_kelly
    risk_budget = min(trade_headroom, kelly_budget, portfolio_headroom, cluster_headroom)
    current_notional = request.current_quantity * request.current_price
    total_notional_headroom = max(0.0, request.maximum_total_notional - current_notional)
    requested_notional = request.initial_notional * request.add_size_fractions[add_index]
    add_notional_cap = min(requested_notional, total_notional_headroom)
    if risk_budget <= 0:
        return _reject("no_risk_headroom", **context)
    if add_notional_cap <= 0:
        return _reject("no_notional_headroom", **context)

    sizing = size_long_paper_position(
        LongPaperSizingRequest(
            equity=request.equity,
            available_cash=request.available_cash,
            entry_price=request.current_price,
            protective_stop=request.protective_stop,
            risk_fraction=risk_budget / request.equity,
            max_notional=add_notional_cap,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
        )
    )
    if not sizing.executable:
        return _reject(f"position_sizing_rejected:{sizing.rejection_reason}", **context)

    accepted_risk = sizing.estimated_loss_at_stop
    return PyramidPlan(
        status="approved_for_review",
        reason="profitable_confirmed_add_within_shared_risk_budget",
        add_index=add_index,
        r_multiple=r_multiple,
        locked_profit=locked_profit,
        current_open_stop_risk=open_risk,
        robust_expectancy_r=expectancy,
        full_kelly_fraction=full_kelly,
        requested_add_notional=requested_notional,
        approved_add_notional=sizing.notional,
        portfolio_risk_after=request.existing_portfolio_stop_risk + accepted_risk,
        cluster_risk_after=request.existing_cluster_stop_risk + accepted_risk,
        protective_stop=request.protective_stop,
        sizing=sizing,
    )
