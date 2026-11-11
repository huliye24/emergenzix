"""Authority-safe adapter from the existing NEMT v4 mother line to BRLA/BRPM."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from .nemt_strategy.nemt_pyramiding_v4 import (
    PyramidPosition, PyramidingControllerV4, PyramidingV4Config,
)

PROTOCOL = "emergenzix-brla-nemt-v4-position-prior-v1"
PriorAction = Literal["HOLD", "TIGHTEN_STOP", "DECREASING_ADD_CANDIDATE"]


@dataclass(frozen=True, slots=True)
class NEMTV4PriorRequest:
    side: int
    initial_entry: float
    initial_stop: float
    initial_notional: float
    current_quantity: float
    total_cost: float
    protective_stop: float
    current_price: float
    atr: float
    equity: float
    add_count: int
    same_direction_confirmed: bool
    best_price: float | None = None

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("NEMT v4 prior requires an existing long or short position")
        if min(self.initial_entry, self.initial_stop, self.initial_notional,
               self.current_quantity, self.total_cost, self.protective_stop,
               self.current_price, self.atr, self.equity) <= 0:
            raise ValueError("NEMT v4 prior requires positive existing-position inputs")
        if self.add_count < 0:
            raise ValueError("add_count cannot be negative")


@dataclass(frozen=True, slots=True)
class NEMTV4PriorAdvice:
    action: PriorAction
    candidate_add_notional: float
    candidate_protective_stop: float
    add_index: int | None
    reason: str
    brpm_required: bool = True
    brgc_required: bool = True
    position_permission: bool = False
    order_permission: bool = False
    automatic_promotion: bool = False


def evaluate_nemt_v4_prior(request: NEMTV4PriorRequest, *,
                           config: PyramidingV4Config = PyramidingV4Config()
                           ) -> NEMTV4PriorAdvice:
    """Return a non-authoritative position-management prior for an open position."""
    position = PyramidPosition(side=request.side,
        initial_entry=request.initial_entry, initial_stop=request.initial_stop,
        initial_notional=request.initial_notional,
        total_quantity=request.current_quantity, total_cost=request.total_cost,
        protective_stop=request.protective_stop, add_count=request.add_count,
        best_price=request.best_price)
    decision = PyramidingControllerV4(config).evaluate(position,
        price=request.current_price, atr=request.atr, equity=request.equity,
        same_direction_confirmed=request.same_direction_confirmed)
    if decision.add_notional > 0:
        action: PriorAction = "DECREASING_ADD_CANDIDATE"
    elif decision.new_stop != request.protective_stop:
        action = "TIGHTEN_STOP"
    else:
        action = "HOLD"
    return NEMTV4PriorAdvice(action, decision.add_notional, decision.new_stop,
                             decision.add_index, decision.reason)


def nemt_v4_prior_manifest() -> dict[str, object]:
    payload = {
        "protocol": PROTOCOL,
        "mother_line": "backend.emergenzix_api.nemt_strategy.nemt_pyramiding_v4",
        "scope": "EXISTING_POSITION_ONLY",
        "advice": ["HOLD", "TIGHTEN_STOP", "DECREASING_ADD_CANDIDATE"],
        "cannot_generate_entry_direction": True,
        "brpm_required": True,
        "brgc_required": True,
        "position_permission": False,
        "order_permission": False,
        "automatic_promotion": False,
    }
    payload["protocol_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return payload
