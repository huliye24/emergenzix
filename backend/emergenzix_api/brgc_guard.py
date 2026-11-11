"""Deterministic PAPER-only BRGC gate; models cannot override these checks."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    decision: str
    approved_paper_target_fraction: float
    reason_code: str
    mode: str = "PAPER"
    order_permission: bool = False
    automatic_promotion: bool = False


def gate_paper_target(
    requested: float,
    *,
    hard_maximum_short_fraction: float = 0.50,
    market_data_fresh: bool,
    account_state_certain: bool,
    risk_state_valid: bool,
) -> GuardResult:
    """Approve only a bounded PAPER target; every unknown condition fails flat."""
    if (not math.isfinite(requested)
            or not math.isfinite(hard_maximum_short_fraction)
            or not 0 < hard_maximum_short_fraction <= .50
            or requested > 0
            or requested < -hard_maximum_short_fraction):
        return GuardResult("REJECT", 0.0, "INVALID_OR_UNBOUNDED_TARGET")
    if not market_data_fresh:
        return GuardResult("REJECT", 0.0, "STALE_MARKET_DATA")
    if not account_state_certain:
        return GuardResult("REJECT", 0.0, "UNCERTAIN_ACCOUNT_STATE")
    if not risk_state_valid:
        return GuardResult("REJECT", 0.0, "INVALID_RISK_STATE")
    return GuardResult("ALLOW_PAPER_TARGET", requested, "ALL_CHECKS_PASS")
