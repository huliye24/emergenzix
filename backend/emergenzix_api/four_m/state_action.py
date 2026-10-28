"""STATE / ACTION public conceptual interface for 4M research.

STATE describes evidence; ACTION describes a *paper research suggestion*.
Neither entity can submit an order. All implementation remains fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .engine import Assessment, Bias, Permission, Regime, Snapshot, Trend, assess


@dataclass(frozen=True)
class State:
    observed_at: datetime
    macro: Bias
    regime: Regime
    price_trend: Trend
    macro_score: float | None
    evidence_notes: tuple[str, ...]


@dataclass(frozen=True)
class Action:
    """An advisory decision, never an executable order or trade permission."""
    recommendation: Permission
    mode: str
    live_trading_enabled: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class StateAction:
    state: State
    action: Action


def evaluate(snapshot: Snapshot) -> StateAction:
    """Apply reviewed 4M v0.1 rules to yield two human-facing concepts.

    No side effects: no exchange calls, credentials, order API or persistence.
    """
    result: Assessment = assess(snapshot)
    return StateAction(
        state=State(
            observed_at=result.as_of,
            macro=result.macro,
            regime=result.regime,
            price_trend=result.trend,
            macro_score=result.macro_score,
            evidence_notes=result.reasons,
        ),
        action=Action(
            recommendation=result.permission,
            mode=result.mode,
            live_trading_enabled=False,
            reasons=result.reasons,
        ),
    )
