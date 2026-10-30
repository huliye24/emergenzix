"""Pure PAPER position sizing; this module cannot create or execute orders.

The stop-risk budget concept is adapted from NEMT v3 commit
f784e6ff5f9bc5465078a85b10f599a5bf716280. Emergenzix adds explicit entry and
exit cost buffers plus cash and product-level notional caps. The output is a
reviewable decision, never an execution permission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


BindingCap = Literal["risk_budget", "available_cash", "max_notional", "rejected"]


@dataclass(frozen=True, slots=True)
class LongPaperSizingRequest:
    """Inputs for one long-only PAPER position-sizing decision."""

    equity: float
    available_cash: float
    entry_price: float
    protective_stop: float
    risk_fraction: float
    max_notional: float
    fee_bps: float = 0.0
    slippage_bps: float = 0.0


@dataclass(frozen=True, slots=True)
class PositionSizingDecision:
    """Auditable sizing output with no side effect or order capability."""

    quantity: float
    notional: float
    risk_budget: float
    estimated_loss_at_stop: float
    risk_per_unit: float
    modeled_entry_fill: float
    modeled_stop_fill: float
    binding_cap: BindingCap
    rejection_reason: str | None = None

    @property
    def executable(self) -> bool:
        return self.quantity > 0.0 and self.rejection_reason is None


def _rejected(reason: str) -> PositionSizingDecision:
    return PositionSizingDecision(
        quantity=0.0,
        notional=0.0,
        risk_budget=0.0,
        estimated_loss_at_stop=0.0,
        risk_per_unit=0.0,
        modeled_entry_fill=0.0,
        modeled_stop_fill=0.0,
        binding_cap="rejected",
        rejection_reason=reason,
    )


def size_long_paper_position(
    request: LongPaperSizingRequest,
) -> PositionSizingDecision:
    """Size a long PAPER position and fail closed on unsafe inputs.

    Slippage is modeled adversely on both entry and stop exit. Fees are charged
    on both modeled fills. Quantity is capped independently by stop-loss risk,
    cash required for the entry fill and fee, and maximum notional.
    """

    values = (
        request.equity,
        request.available_cash,
        request.entry_price,
        request.protective_stop,
        request.risk_fraction,
        request.max_notional,
        request.fee_bps,
        request.slippage_bps,
    )
    if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
        return _rejected("all sizing inputs must be finite numbers")
    if request.equity <= 0.0:
        return _rejected("equity must be positive")
    if request.available_cash <= 0.0:
        return _rejected("available cash must be positive")
    if request.entry_price <= 0.0 or request.protective_stop <= 0.0:
        return _rejected("entry and stop prices must be positive")
    if request.protective_stop >= request.entry_price:
        return _rejected("a long protective stop must be below entry")
    if not 0.0 < request.risk_fraction <= 0.1:
        return _rejected("risk fraction must be in (0, 0.1]")
    if request.max_notional <= 0.0:
        return _rejected("maximum notional must be positive")
    if not 0.0 <= request.fee_bps <= 10_000.0:
        return _rejected("fee bps must be in [0, 10000]")
    if not 0.0 <= request.slippage_bps < 10_000.0:
        return _rejected("slippage bps must be in [0, 10000)")

    fee_rate = request.fee_bps / 10_000.0
    slippage_rate = request.slippage_bps / 10_000.0
    entry_fill = request.entry_price * (1.0 + slippage_rate)
    stop_fill = request.protective_stop * (1.0 - slippage_rate)
    risk_per_unit = (
        entry_fill - stop_fill + entry_fill * fee_rate + stop_fill * fee_rate
    )
    if risk_per_unit <= 0.0 or not math.isfinite(risk_per_unit):
        return _rejected("modeled stop risk must be positive and finite")

    risk_budget = request.equity * request.risk_fraction
    entry_cash_per_unit = entry_fill * (1.0 + fee_rate)
    caps = {
        "risk_budget": risk_budget / risk_per_unit,
        "available_cash": request.available_cash / entry_cash_per_unit,
        "max_notional": request.max_notional / entry_fill,
    }
    binding_cap = min(caps, key=caps.__getitem__)
    quantity = caps[binding_cap]
    if quantity <= 0.0 or not math.isfinite(quantity):
        return _rejected("no positive quantity is available within the limits")

    notional = quantity * entry_fill
    estimated_loss = quantity * risk_per_unit
    return PositionSizingDecision(
        quantity=quantity,
        notional=notional,
        risk_budget=risk_budget,
        estimated_loss_at_stop=estimated_loss,
        risk_per_unit=risk_per_unit,
        modeled_entry_fill=entry_fill,
        modeled_stop_fill=stop_fill,
        binding_cap=binding_cap,  # type: ignore[arg-type]
    )
