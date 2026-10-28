"""Four-layer market intelligence: deterministic, fail-closed PAPER research.

Inputs are precomputed, point-in-time features. This module neither fetches
data nor emits executable orders; permissions are advisory research labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite


class Bias(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class Regime(str, Enum):
    ACCUMULATION = "accumulation"
    EXPANSION = "expansion"
    DISTRIBUTION = "distribution"
    CONTRACTION = "contraction"
    UNKNOWN = "unknown"


class Trend(str, Enum):
    UP = "up"
    RANGE = "range"
    DOWN = "down"
    UNKNOWN = "unknown"


class Permission(str, Enum):
    REVIEW_LONG = "review_long"
    REVIEW_SHORT = "review_short"
    WAIT = "wait"
    HALT = "halt"


@dataclass(frozen=True)
class Snapshot:
    """An observation available at as_of; missing votes must remain None.

    Macro votes: +1 / 0 / -1 for policy, real yields, DXY and liquidity.
    market_trend: 1 upward, -1 downward, 0 range.
    valuation: -1 cheap, +1 expensive, 0 neutral.
    holder_flow / etf_flow: +1 accumulation, -1 distribution, 0 flat.
    price_trend must be independently derived from completed candles.
    """
    as_of: datetime
    macro_votes: tuple[int | None, int | None, int | None, int | None]
    market_trend: int | None
    valuation: int | None
    holder_flow: int | None
    etf_flow: int | None
    price_trend: Trend
    data_fresh: bool
    risk_ok: bool
    risk_reason: str = ""


@dataclass(frozen=True)
class Assessment:
    as_of: datetime
    macro: Bias
    macro_score: float | None
    regime: Regime
    trend: Trend
    permission: Permission
    reasons: tuple[str, ...]
    mode: str = "PAPER_RESEARCH"
    live_trading_enabled: bool = False


def _vote(value: int | None) -> bool:
    return value is None or (type(value) is int and value in (-1, 0, 1))


def assess(snapshot: Snapshot) -> Assessment:
    """Compute a transparent scenario and advisory permission; never order."""
    if snapshot.as_of.tzinfo is None or snapshot.as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if snapshot.as_of.astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise ValueError("as_of cannot be in the future")
    votes = (*snapshot.macro_votes, snapshot.market_trend,
             snapshot.valuation, snapshot.holder_flow, snapshot.etf_flow)
    if len(snapshot.macro_votes) != 4 or not all(_vote(v) for v in votes):
        raise ValueError("votes must be -1, 0, +1, or None")
    if not isinstance(snapshot.price_trend, Trend):
        raise ValueError("price_trend must be a Trend enum")
    if not isinstance(snapshot.data_fresh, bool) or not isinstance(snapshot.risk_ok, bool):
        raise ValueError("risk and freshness flags must be boolean")

    reasons: list[str] = []
    macro_score = None
    macro = Bias.UNKNOWN
    if all(v is not None for v in snapshot.macro_votes):
        macro_score = sum(snapshot.macro_votes) / 4
        macro = (Bias.POSITIVE if macro_score >= 0.5 else
                 Bias.NEGATIVE if macro_score <= -0.5 else Bias.NEUTRAL)
    else:
        reasons.append("missing_macro_observation")

    regime = Regime.UNKNOWN
    mt, val, holders, etf = (snapshot.market_trend, snapshot.valuation,
                             snapshot.holder_flow, snapshot.etf_flow)
    if all(v is not None for v in (mt, val, holders, etf)):
        demand = holders + etf
        if mt == 1 and demand >= 0:
            regime = Regime.EXPANSION
        elif mt == -1 and demand <= 0:
            regime = Regime.CONTRACTION
        elif mt <= 0 and val == -1 and demand > 0:
            regime = Regime.ACCUMULATION
        elif mt >= 0 and val == 1 and demand < 0:
            regime = Regime.DISTRIBUTION
        else:
            reasons.append("regime_conflict")
    else:
        reasons.append("missing_market_observation")

    permission = Permission.WAIT
    if not snapshot.data_fresh or not snapshot.risk_ok:
        permission = Permission.HALT
        reasons.append("stale_data" if not snapshot.data_fresh else "risk_veto")
        if snapshot.risk_reason:
            reasons.append(snapshot.risk_reason)
    elif macro == Bias.UNKNOWN or regime == Regime.UNKNOWN or snapshot.price_trend == Trend.UNKNOWN:
        reasons.append("insufficient_evidence")
    elif (macro != Bias.NEGATIVE and regime in (Regime.EXPANSION, Regime.ACCUMULATION)
          and snapshot.price_trend == Trend.UP):
        permission = Permission.REVIEW_LONG
    elif (macro != Bias.POSITIVE and regime in (Regime.CONTRACTION, Regime.DISTRIBUTION)
          and snapshot.price_trend == Trend.DOWN):
        permission = Permission.REVIEW_SHORT
    else:
        reasons.append("no_aligned_scenario")

    return Assessment(snapshot.as_of, macro, macro_score, regime,
                      snapshot.price_trend, permission, tuple(reasons))
