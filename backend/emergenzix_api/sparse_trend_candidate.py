"""S-004.A: causal sparse-trend candidate classification (research only).

Candidate is not an order, a profitability forecast, or permission to trade.
Thresholds are exploratory defaults, not fitted to the observed 2022-2025 data.
Only closed, contiguous UTC 4-hour bars can contribute to a decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Literal, Sequence

from .schemas import Bar

State = Literal["OBSERVE", "CANDIDATE", "REJECT"]
STEP = timedelta(hours=4)


@dataclass(frozen=True)
class SparseTrendConfig:
    short_bars: int = 42      # 7 days
    long_bars: int = 126      # 21 days
    min_long_return: float = 0.06
    min_efficiency: float = 0.30

    def __post_init__(self) -> None:
        if self.short_bars < 2 or self.long_bars <= self.short_bars:
            raise ValueError("invalid trend horizons")
        if self.min_long_return < 0 or not 0 <= self.min_efficiency <= 1:
            raise ValueError("invalid experimental thresholds")


@dataclass(frozen=True)
class SparseTrendDecision:
    state: State
    reason: str
    last_closed_at_utc: str | None
    long_return: float | None = None
    efficiency: float | None = None


def classify_sparse_trend(
    bars: Sequence[Bar], *, as_of: datetime,
    config: SparseTrendConfig = SparseTrendConfig(),
) -> SparseTrendDecision:
    """Classify a LONG candidate from information available at as_of.

    Candles are timestamped at their opening time; the entry evaluation uses
    only candles where open_time + 4 hours <= as_of. REJECT is a fully observed
    but disqualified trend; OBSERVE means insufficient/unusable context.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    now = as_of.astimezone(timezone.utc)
    visible: list[Bar] = []
    previous = None
    for bar in bars:
        ts = bar.timestamp.astimezone(timezone.utc)
        if ts.minute or ts.second or ts.microsecond or ts.hour % 4:
            raise ValueError("expected UTC-aligned 4h open timestamps")
        if previous is not None and ts <= previous:
            raise ValueError("bars must be strictly ordered without duplicates")
        previous = ts
        if ts + STEP <= now:
            visible.append(bar)
    if len(visible) < config.long_bars + 1:
        return SparseTrendDecision("OBSERVE", "insufficient_closed_history", None)
    window = visible[-(config.long_bars + 1):]
    times = [b.timestamp.astimezone(timezone.utc) for b in window]
    if any(b - a != STEP for a, b in zip(times, times[1:])):
        return SparseTrendDecision("OBSERVE", "missing_4h_candle", None)
    if times[-1] + STEP != now.replace(
        hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0
    ):
        return SparseTrendDecision("OBSERVE", "stale_data", None)
    closes = [b.close for b in window]
    if any(not isfinite(v) or v <= 0 for v in closes):
        return SparseTrendDecision("OBSERVE", "invalid_prices", None)
    long_return = closes[-1] / closes[0] - 1.0
    short_return = closes[-1] / closes[-1 - config.short_bars] - 1.0
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    efficiency = abs(closes[-1] - closes[0]) / path if path else 0.0
    stamp = (times[-1] + STEP).isoformat()
    if long_return < config.min_long_return:
        return SparseTrendDecision("REJECT", "insufficient_long_trend", stamp, long_return, efficiency)
    if short_return <= 0:
        return SparseTrendDecision("REJECT", "short_trend_not_confirmed", stamp, long_return, efficiency)
    if efficiency < config.min_efficiency:
        return SparseTrendDecision("REJECT", "inefficient_trend_path", stamp, long_return, efficiency)
    return SparseTrendDecision("CANDIDATE", "trend_candidate_only", stamp, long_return, efficiency)
