"""S-004 arm A: causal sparse-trend candidate classifier (research only).

Implements the contract mandated by
``strategies/candidates/S-004-sparse-trend-pyramid-hedge.md`` §52:

    outputs OBSERVE / CANDIDATE / REJECT
    reports the last *completed* weekly timestamp it was allowed to see
    reports a rejection reason
    is covered by synthetic time-series tests

Causality rules:
  * only completed Monday-Sunday UTC weeks are visible;
  * the newest visible week must end at or before ``as_of``;
  * a missing or incomplete week breaks the chain and forces REJECT (fail closed).

No orders, no sizing, no exit logic. This module only classifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from ..schemas import Bar
from ..weekly_regime_filter import completed_week_closes

OBSERVE = "OBSERVE"
CANDIDATE = "CANDIDATE"
REJECT = "REJECT"

FOUR_HOURS = timedelta(hours=4)
WEEK = timedelta(days=7)
MIN_WEEKS = 53           # need 52 completed weeks plus the current close
ATR_BARS = 120
EXPANSION_LOOKBACK = 78  # 52 weeks of weekly ATR samples ≈ 78 four-hour bars per week? no:
# ATR expansion is measured on 4h bars over the trailing 52 weeks.
EXPANSION_BARS = 52 * 42


@dataclass(frozen=True)
class TrendConfig:
    """Frozen thresholds. Selected on the TRAIN window only."""

    name: str
    min_agreement: int = 3          # of the 13/26/52-week direction windows
    min_efficiency: float = 0.0     # |net move| / sum|weekly moves| over 26 weeks
    min_expansion: float = 0.0      # current ATR(120) / median ATR(120) over 52 weeks
    long_only: bool = False


@dataclass
class TrendVerdict:
    status: str
    direction: int
    reason: str
    last_completed_week: str | None
    features: dict = field(default_factory=dict)


def _weekly_features(closes: list[float], direction: int) -> dict:
    """Direction agreement across 13/26/52 completed-week windows."""
    windows = {"w13": 13, "w26": 26, "w52": 52}
    agreements = {}
    for key, span in windows.items():
        if len(closes) <= span:
            agreements[key] = None
            continue
        move = closes[-1] - closes[-1 - span]
        agreements[key] = 1 if move > 0 else (-1 if move < 0 else 0)
    usable = [v for v in agreements.values() if v is not None]
    agreement = sum(1 for v in usable if v == direction)

    # path efficiency over the last 26 weekly closes
    span = 26
    if len(closes) > span:
        segment = closes[-1 - span:]
        net = abs(segment[-1] - segment[0])
        gross = sum(abs(b - a) for a, b in zip(segment, segment[1:]))
        efficiency = net / gross if gross > 0 else 0.0
        directional = 1 if (segment[-1] - segment[0]) * direction > 0 else 0
    else:
        efficiency, directional = 0.0, 0

    return {"agreements": agreements, "agreement": agreement,
            "usable_windows": len(usable), "efficiency": round(efficiency, 4),
            "directional": directional}


def _atr_expansion(bars: Sequence[Bar], as_of: datetime) -> float | None:
    """Current ATR(120) divided by the median ATR(120) over the trailing 52 weeks."""
    as_of = as_of.astimezone(timezone.utc)
    visible = [b for b in bars if b.timestamp + FOUR_HOURS <= as_of]
    if len(visible) < ATR_BARS + 50:
        return None

    def true_range(index: int) -> float:
        bar, prev = visible[index], visible[index - 1]
        return max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))

    latest = sum(true_range(i) for i in range(len(visible) - ATR_BARS, len(visible))) / ATR_BARS
    window = min(EXPANSION_BARS, len(visible) - ATR_BARS)
    samples = []
    for offset in range(0, window, ATR_BARS):
        end = len(visible) - offset
        start = end - ATR_BARS
        if start < 1:
            break
        samples.append(sum(true_range(i) for i in range(start, end)) / ATR_BARS)
    if len(samples) < 2:
        return None
    samples.sort()
    median = samples[len(samples) // 2]
    return latest / median if median > 0 else None


def classify(bars: Sequence[Bar], *, as_of: datetime, direction: int,
             config: TrendConfig) -> TrendVerdict:
    """Return OBSERVE / CANDIDATE / REJECT for one direction at ``as_of``."""
    if direction not in (1, -1):
        raise ValueError("direction must be 1 (long) or -1 (short)")
    if config.long_only and direction == -1:
        return TrendVerdict(REJECT, direction, "long_only_arm", None, {})
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")

    try:
        weeks = completed_week_closes(bars, as_of=as_of)
    except ValueError as exc:
        return TrendVerdict(REJECT, direction, f"invalid_series:{exc}", None, {})
    if not weeks:
        return TrendVerdict(OBSERVE, direction, "no_completed_week", None, {})
    last_week = weeks[-1][0] - WEEK     # week start of the last completed week
    stamp = last_week.date().isoformat()
    if len(weeks) < MIN_WEEKS:
        return TrendVerdict(REJECT, direction, "insufficient_history", stamp, {})

    closes = [close for _, close in weeks]
    # Chain integrity: the last 53 completed weeks must be contiguous.
    for earlier, later in zip(weeks[-MIN_WEEKS:], weeks[-MIN_WEEKS + 1:]):
        if later[0] - earlier[0] != WEEK:
            return TrendVerdict(REJECT, direction, "broken_week_chain", stamp, {})

    features = _weekly_features(closes, direction)
    if features["usable_windows"] < 3:
        return TrendVerdict(REJECT, direction, "insufficient_windows", stamp, features)
    if features["directional"] == 0:
        return TrendVerdict(REJECT, direction, "direction_mismatch_26w", stamp, features)
    if features["agreement"] < config.min_agreement:
        return TrendVerdict(REJECT, direction,
                            f"agreement_{features['agreement']}of3", stamp, features)
    if features["efficiency"] < config.min_efficiency:
        return TrendVerdict(REJECT, direction,
                            f"efficiency_{features['efficiency']:.3f}", stamp, features)

    expansion = _atr_expansion(bars, as_of)
    features["atr_expansion"] = round(expansion, 4) if expansion else None
    if config.min_expansion > 0:
        if expansion is None:
            return TrendVerdict(REJECT, direction, "expansion_unavailable", stamp, features)
        if expansion < config.min_expansion:
            return TrendVerdict(REJECT, direction,
                                f"expansion_{expansion:.3f}", stamp, features)

    return TrendVerdict(CANDIDATE, direction, "candidate", stamp, features)


# Configurations declared before any 2022-2025 evaluation; the winner is chosen
# on the TRAIN window only (see the preregistration for this arm).
CONFIGS: dict[str, TrendConfig] = {
    "A1": TrendConfig("A1_agree3_eff0", min_agreement=3, min_efficiency=0.0),
    "A2": TrendConfig("A2_agree3_eff20", min_agreement=3, min_efficiency=0.20),
    "A3": TrendConfig("A3_agree3_eff35", min_agreement=3, min_efficiency=0.35),
    "A4": TrendConfig("A4_agree2_eff20", min_agreement=2, min_efficiency=0.20),
    "A5": TrendConfig("A5_agree3_eff35_exp11", min_agreement=3,
                      min_efficiency=0.35, min_expansion=1.10),
}
