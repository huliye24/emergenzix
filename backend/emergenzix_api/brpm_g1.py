"""BRPM-G1 short-position risk budget and append-only release ledger."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Literal, Sequence


Decision = Literal["APPROVE_PROBE", "APPROVE_ADD", "APPROVE_HOLD", "APPROVE_REDUCE",
                   "APPROVE_EXIT", "REJECT"]


@dataclass(frozen=True, slots=True)
class BRPMRequest:
    campaign_id: str
    equity: float
    mark_price: float
    current_target_fraction: float
    requested_target_fraction: float
    protective_stop_price: float | None
    maximum_campaign_risk_fraction: float = .02
    existing_other_positions_risk_fraction: float = 0.0
    maximum_portfolio_risk_fraction: float = .06
    maximum_short_fraction: float = .50
    cost_rate: float = .0015
    trend_maturity: float = 0.0
    entry_location_quality: float = 1.0


@dataclass(frozen=True, slots=True)
class RiskLedgerEntry:
    campaign_id: str
    sequence: int
    previous_entry_sha256: str | None
    decision: Decision
    reason: str
    current_target_fraction: float
    requested_target_fraction: float
    approved_target_fraction: float
    mark_price: float
    protective_stop_price: float | None
    risk_budget_fraction: float
    prior_worst_loss_fraction: float
    pre_add_worst_loss_fraction: float
    final_worst_loss_fraction: float
    portfolio_worst_loss_fraction: float
    released_risk_fraction: float
    remaining_risk_budget_fraction: float
    remaining_portfolio_risk_budget_fraction: float
    mode: str = "PAPER"
    automatic_promotion: bool = False
    order_permission: bool = False
    entry_sha256: str = ""


def _fingerprint(entry: RiskLedgerEntry) -> str:
    payload = asdict(replace(entry, entry_sha256=""))
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def verify_risk_ledger(entries: Sequence[RiskLedgerEntry]) -> bool:
    previous = None
    for sequence, entry in enumerate(entries):
        if entry.sequence != sequence or entry.previous_entry_sha256 != previous:
            return False
        if entry.entry_sha256 != _fingerprint(entry):
            return False
        previous = entry.entry_sha256
    return True


def _worst_loss(target: float, mark: float, stop: float, cost_rate: float,
                *, added_fraction: float = 0.0) -> float:
    adverse_move = stop / mark - 1.0
    exit_cost = abs(target) * cost_rate
    entry_cost = max(added_fraction, 0.0) * cost_rate
    return abs(target) * adverse_move + exit_cost + entry_cost


def _entry(request: BRPMRequest, previous: RiskLedgerEntry | None, *,
           decision: Decision, reason: str, approved: float, prior_risk: float,
           pre_add_risk: float, final_risk: float) -> RiskLedgerEntry:
    released = max(prior_risk - pre_add_risk, 0.0)
    base = RiskLedgerEntry(
        campaign_id=request.campaign_id,
        sequence=0 if previous is None else previous.sequence + 1,
        previous_entry_sha256=None if previous is None else previous.entry_sha256,
        decision=decision, reason=reason,
        current_target_fraction=request.current_target_fraction,
        requested_target_fraction=request.requested_target_fraction,
        approved_target_fraction=approved, mark_price=request.mark_price,
        protective_stop_price=request.protective_stop_price,
        risk_budget_fraction=request.maximum_campaign_risk_fraction,
        prior_worst_loss_fraction=prior_risk,
        pre_add_worst_loss_fraction=pre_add_risk,
        final_worst_loss_fraction=final_risk,
        portfolio_worst_loss_fraction=(
            request.existing_other_positions_risk_fraction + final_risk),
        released_risk_fraction=released,
        remaining_risk_budget_fraction=max(
            request.maximum_campaign_risk_fraction - final_risk, 0.0),
        remaining_portfolio_risk_budget_fraction=max(
            request.maximum_portfolio_risk_fraction
            - request.existing_other_positions_risk_fraction - final_risk, 0.0),
    )
    return replace(base, entry_sha256=_fingerprint(base))


def _reject(request: BRPMRequest, previous: RiskLedgerEntry | None,
            reason: str) -> RiskLedgerEntry:
    prior = 0.0 if previous is None else previous.final_worst_loss_fraction
    return _entry(request, previous, decision="REJECT", reason=reason,
                  approved=request.current_target_fraction, prior_risk=prior,
                  pre_add_risk=prior, final_risk=prior)


def allocate_short_risk(request: BRPMRequest,
                        ledger: Sequence[RiskLedgerEntry] = ()) -> RiskLedgerEntry:
    """Return an auditable PAPER allocation; additions require lower total risk."""
    previous = ledger[-1] if ledger else None
    numeric = (request.equity, request.mark_price, request.current_target_fraction,
               request.requested_target_fraction, request.maximum_campaign_risk_fraction,
               request.existing_other_positions_risk_fraction,
               request.maximum_portfolio_risk_fraction,
               request.maximum_short_fraction, request.cost_rate,
               request.trend_maturity, request.entry_location_quality)
    if (not request.campaign_id.strip()
            or any(isinstance(value, bool) or not math.isfinite(value) for value in numeric)
            or request.equity <= 0 or request.mark_price <= 0
            or not 0 < request.maximum_campaign_risk_fraction <= .10
            or request.existing_other_positions_risk_fraction < 0
            or not 0 < request.maximum_portfolio_risk_fraction <= .25
            or request.existing_other_positions_risk_fraction
                > request.maximum_portfolio_risk_fraction
            or not 0 < request.maximum_short_fraction <= .50
            or not 0 <= request.cost_rate <= .05
            or not 0 <= request.trend_maturity <= 1
            or not 0 <= request.entry_location_quality <= 1
            or not -request.maximum_short_fraction <= request.current_target_fraction <= 0
            or not -request.maximum_short_fraction <= request.requested_target_fraction <= 0):
        return _reject(request, previous, "INVALID_OR_UNBOUNDED_INPUT")
    if ledger and (not verify_risk_ledger(ledger)
                   or previous is None
                   or previous.campaign_id != request.campaign_id
                   or previous.approved_target_fraction != request.current_target_fraction):
        return _reject(request, previous, "INVALID_OR_MISMATCHED_LEDGER")

    current, requested = request.current_target_fraction, request.requested_target_fraction
    if requested == 0:
        return _entry(request, previous, decision="APPROVE_EXIT", reason="RISK_REMOVED",
                      approved=0.0, prior_risk=0.0 if previous is None else previous.final_worst_loss_fraction,
                      pre_add_risk=0.0, final_risk=0.0)
    if requested > current:
        stop = request.protective_stop_price
        if (previous is None or stop is None or not math.isfinite(stop)
                or stop <= request.mark_price):
            return _reject(request, previous, "PARTIAL_REDUCTION_REQUIRES_VALID_STOP_AND_LEDGER")
        prior = previous.final_worst_loss_fraction
        final = _worst_loss(requested, request.mark_price, stop, request.cost_rate)
        if final > prior:
            return _reject(request, previous, "PARTIAL_REDUCTION_MUST_NOT_INCREASE_RISK")
        return _entry(request, previous, decision="APPROVE_REDUCE", reason="RISK_REDUCED",
                      approved=requested, prior_risk=prior, pre_add_risk=prior,
                      final_risk=final)
    if requested == current:
        prior = 0.0 if previous is None else previous.final_worst_loss_fraction
        return _entry(request, previous, decision="APPROVE_HOLD", reason="NO_RISK_ADDED",
                      approved=current, prior_risk=prior, pre_add_risk=prior, final_risk=prior)

    if request.trend_maturity >= .70:
        return _reject(request, previous, "LATE_ENTRY_FORBIDDEN" if current == 0
                       else "MATURE_TREND_ADD_FORBIDDEN")
    if current == 0 and request.entry_location_quality < .20:
        return _reject(request, previous, "POOR_ENTRY_LOCATION")

    stop = request.protective_stop_price
    if stop is None or not math.isfinite(stop) or stop <= request.mark_price:
        return _reject(request, previous, "VALID_SHORT_STOP_REQUIRED")
    added = abs(requested) - abs(current)
    final_risk = _worst_loss(requested, request.mark_price, stop,
                             request.cost_rate, added_fraction=added)
    if current == 0:
        if requested < -.10:
            return _reject(request, previous, "INITIAL_PROBE_LIMIT")
        if final_risk > request.maximum_campaign_risk_fraction:
            return _reject(request, previous, "RISK_BUDGET_EXCEEDED")
        if (request.existing_other_positions_risk_fraction + final_risk
                > request.maximum_portfolio_risk_fraction):
            return _reject(request, previous, "PORTFOLIO_RISK_BUDGET_EXCEEDED")
        return _entry(request, previous, decision="APPROVE_PROBE", reason="STOP_BOUNDED_PROBE",
                      approved=requested, prior_risk=0.0, pre_add_risk=0.0,
                      final_risk=final_risk)

    if previous is None or previous.protective_stop_price is None:
        return _reject(request, previous, "PRIOR_STOP_AND_LEDGER_REQUIRED_FOR_ADD")
    prior_risk = previous.final_worst_loss_fraction
    if stop >= previous.protective_stop_price:
        return _reject(request, previous, "STOP_NOT_TIGHTENED")
    pre_add_risk = _worst_loss(current, request.mark_price, stop, request.cost_rate)
    if pre_add_risk >= prior_risk:
        return _reject(request, previous, "NO_RISK_RELEASED")
    if final_risk >= prior_risk:
        return _reject(request, previous, "TOTAL_RISK_NOT_LOWER_AFTER_ADD")
    if final_risk > request.maximum_campaign_risk_fraction:
        return _reject(request, previous, "RISK_BUDGET_EXCEEDED")
    if (request.existing_other_positions_risk_fraction + final_risk
            > request.maximum_portfolio_risk_fraction):
        return _reject(request, previous, "PORTFOLIO_RISK_BUDGET_EXCEEDED")
    return _entry(request, previous, decision="APPROVE_ADD",
                  reason="STOP_TIGHTENED_AND_TOTAL_RISK_LOWER",
                  approved=requested, prior_risk=prior_risk,
                  pre_add_risk=pre_add_risk, final_risk=final_risk)
