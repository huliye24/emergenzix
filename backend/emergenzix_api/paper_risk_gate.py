"""Fail-closed operational risk gate for PAPER order intents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PaperRiskInputs:
    now: datetime
    market_data_at: datetime | None
    balance_at: datetime | None
    max_market_age_seconds: float
    max_balance_age_seconds: float
    balance_known: bool
    account_frozen: bool
    uncertain_order_count: int
    unprotected_position_count: int
    execution_mode: str = "PAPER"


@dataclass(frozen=True, slots=True)
class PaperRiskSnapshot:
    allow_new_orders: bool
    allow_cancel_orders: bool
    allow_reduce_risk: bool
    reasons: tuple[str, ...]


def evaluate_paper_risk(inputs: PaperRiskInputs) -> PaperRiskSnapshot:
    reasons: list[str] = []
    if inputs.execution_mode != "PAPER":
        reasons.append("execution_mode_not_paper")
    if inputs.now.tzinfo is None:
        reasons.append("now_not_timezone_aware")
    if inputs.max_market_age_seconds <= 0 or inputs.max_balance_age_seconds <= 0:
        reasons.append("invalid_freshness_limit")
    if inputs.uncertain_order_count < 0 or inputs.unprotected_position_count < 0:
        reasons.append("invalid_risk_count")
    if inputs.account_frozen:
        reasons.append("account_frozen")
    if not inputs.balance_known:
        reasons.append("balance_unknown")
    if inputs.uncertain_order_count:
        reasons.append("uncertain_order_state")
    if inputs.unprotected_position_count:
        reasons.append("protective_stop_missing")

    for value, label, limit in (
        (inputs.market_data_at, "market_data", inputs.max_market_age_seconds),
        (inputs.balance_at, "balance", inputs.max_balance_age_seconds),
    ):
        if value is None:
            reasons.append(f"{label}_missing")
        elif value.tzinfo is None:
            reasons.append(f"{label}_not_timezone_aware")
        elif inputs.now.tzinfo is not None:
            age = (inputs.now - value).total_seconds()
            if age < 0:
                reasons.append(f"{label}_from_future")
            elif age > limit:
                reasons.append(f"{label}_stale")

    return PaperRiskSnapshot(
        allow_new_orders=not reasons,
        # Emergency controls must remain available when risk is elevated.
        allow_cancel_orders=True,
        allow_reduce_risk=True,
        reasons=tuple(reasons),
    )
