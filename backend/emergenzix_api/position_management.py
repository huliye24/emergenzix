"""Pure long PAPER position-management plans for domains 4-6.

This module manages an already-open position. It does not generate alpha,
create orders, mutate account state, or grant execution permission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


ManagementStatus = Literal["approved_for_review", "exit_required", "rejected"]


@dataclass(frozen=True, slots=True)
class LongPositionManagementRequest:
    strategy_version_id: str
    position_id: str
    current_price: float
    high_watermark: float
    average_entry: float
    initial_stop: float
    current_stop: float
    atr: float
    atr_multiple: float
    current_quantity: float
    completed_partial_exits: int = 0
    break_even_at_r: float = 1.0
    partial_exit_at_r: tuple[float, ...] = (2.0, 3.0)
    partial_exit_fractions: tuple[float, ...] = (0.25, 0.25)


@dataclass(frozen=True, slots=True)
class PositionManagementPlan:
    status: ManagementStatus
    reason: str
    r_multiple: float
    previous_stop: float
    proposed_stop: float
    stop_changed: bool
    partial_exit_index: int | None
    partial_exit_fraction: float
    partial_exit_quantity: float
    remaining_quantity_after_partial: float
    creates_order: bool = False


def _plan(
    request: LongPositionManagementRequest,
    status: ManagementStatus,
    reason: str,
    *,
    r_multiple: float = 0.0,
    proposed_stop: float | None = None,
    partial_exit_index: int | None = None,
    partial_exit_fraction: float = 0.0,
) -> PositionManagementPlan:
    stop = request.current_stop if proposed_stop is None else proposed_stop
    exit_quantity = request.current_quantity * partial_exit_fraction
    return PositionManagementPlan(
        status=status,
        reason=reason,
        r_multiple=r_multiple,
        previous_stop=request.current_stop,
        proposed_stop=stop,
        stop_changed=stop > request.current_stop,
        partial_exit_index=partial_exit_index,
        partial_exit_fraction=partial_exit_fraction,
        partial_exit_quantity=exit_quantity,
        remaining_quantity_after_partial=request.current_quantity - exit_quantity,
    )


def plan_long_position_management(
    request: LongPositionManagementRequest,
) -> PositionManagementPlan:
    """Plan a monotone stop update and, at most, the next partial exit.

    The ATR candidate is based on the observed high-water mark. A break-even
    floor becomes eligible only after the frozen R threshold is reached. The
    resulting stop can tighten but can never widen or cross the current price.
    Partial exits are sequential and their fractions apply to current quantity.
    """

    numeric = (
        request.current_price,
        request.high_watermark,
        request.average_entry,
        request.initial_stop,
        request.current_stop,
        request.atr,
        request.atr_multiple,
        request.current_quantity,
        request.break_even_at_r,
        *request.partial_exit_at_r,
        *request.partial_exit_fractions,
    )
    if not request.strategy_version_id.strip() or not request.position_id.strip():
        return _plan(request, "rejected", "strategy_version_id and position_id are required")
    if any(isinstance(value, bool) or not math.isfinite(value) for value in numeric):
        return _plan(request, "rejected", "all numeric inputs must be finite")
    if min(
        request.current_price, request.high_watermark, request.average_entry,
        request.initial_stop, request.current_stop, request.atr,
        request.atr_multiple, request.current_quantity,
    ) <= 0:
        return _plan(request, "rejected", "prices, ATR, multiplier, and quantity must be positive")
    if request.initial_stop >= request.average_entry:
        return _plan(request, "rejected", "initial long stop must be below average entry")
    if request.current_stop < request.initial_stop:
        return _plan(request, "rejected", "current stop cannot be below initial stop")
    if request.high_watermark < request.current_price:
        return _plan(request, "rejected", "high watermark cannot be below current price")
    if request.completed_partial_exits < 0 or isinstance(request.completed_partial_exits, bool):
        return _plan(request, "rejected", "completed partial exits must be a non-negative integer")
    if len(request.partial_exit_at_r) != len(request.partial_exit_fractions):
        return _plan(request, "rejected", "partial exit thresholds and fractions must align")
    if any(threshold <= 0 for threshold in request.partial_exit_at_r):
        return _plan(request, "rejected", "partial exit thresholds must be positive")
    if tuple(sorted(request.partial_exit_at_r)) != request.partial_exit_at_r:
        return _plan(request, "rejected", "partial exit thresholds must be increasing")
    if any(not 0 < fraction < 1 for fraction in request.partial_exit_fractions):
        return _plan(request, "rejected", "partial exit fractions must be in (0, 1)")
    if request.completed_partial_exits > len(request.partial_exit_at_r):
        return _plan(request, "rejected", "completed partial exits exceed the frozen schedule")

    initial_risk = request.average_entry - request.initial_stop
    r_multiple = (request.current_price - request.average_entry) / initial_risk
    if request.current_price <= request.current_stop:
        return _plan(
            request, "exit_required", "protective_stop_reached_or_crossed",
            r_multiple=r_multiple,
        )

    atr_candidate = request.high_watermark - request.atr_multiple * request.atr
    candidate = max(request.current_stop, request.initial_stop, atr_candidate)
    if r_multiple >= request.break_even_at_r:
        candidate = max(candidate, request.average_entry)
    # A plan must never place a long stop at or above the observed price.
    proposed_stop = min(candidate, math.nextafter(request.current_price, 0.0))
    proposed_stop = max(request.current_stop, proposed_stop)

    partial_index = None
    partial_fraction = 0.0
    if request.completed_partial_exits < len(request.partial_exit_at_r):
        next_index = request.completed_partial_exits
        if r_multiple >= request.partial_exit_at_r[next_index]:
            partial_index = next_index
            partial_fraction = request.partial_exit_fractions[next_index]

    reason = "hold_position"
    if proposed_stop > request.current_stop and partial_index is not None:
        reason = "tighten_stop_and_review_partial_exit"
    elif proposed_stop > request.current_stop:
        reason = "tighten_stop"
    elif partial_index is not None:
        reason = "review_partial_exit"
    return _plan(
        request,
        "approved_for_review",
        reason,
        r_multiple=r_multiple,
        proposed_stop=proposed_stop,
        partial_exit_index=partial_index,
        partial_exit_fraction=partial_fraction,
    )
