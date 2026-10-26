"""S-004.A.3 historical, pre-entry features; no trade permissions.

Four-hour candle timestamps refer to UTC bar OPEN. as_of is the signal
decision cutoff; bars closing after it are invisible. No future outcomes used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite, log, sqrt
from typing import Sequence

from .schemas import Bar

STEP = timedelta(hours=4)
REQUIRED_BARS = 127


@dataclass(frozen=True)
class EarlyTrendFeatures:
    feature_cutoff_utc: str
    feature_status: str
    reason: str
    trend_age_bars: int | None = None
    efficiency_42: float | None = None
    vol_ratio_42_126: float | None = None
    range_position_126: float | None = None


def _population_std(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sqrt(sum((x - mean) ** 2 for x in values) / len(values))


def extract_early_trend_features(
    bars: Sequence[Bar], *, as_of: datetime,
    direction: str, directional_states: Sequence[str] | None = None,
) -> EarlyTrendFeatures:
    """Compute historical features, never a verdict about future profitability.

    directional_states must be the historical, causal Donchian+NEMT direction
    state for each *input* bar, produced by the same pinned candidate engine.
    Do not infer trend age from future trade outcomes or price direction alone.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    if directional_states is not None and len(directional_states) != len(bars):
        raise ValueError("directional_states must match bars length")
    cutoff = as_of.astimezone(timezone.utc)
    visible: list[Bar] = []
    visible_states: list[str] = []
    prev = None
    for i, bar in enumerate(bars):
        ts = bar.timestamp.astimezone(timezone.utc)
        if ts.minute or ts.second or ts.microsecond or ts.hour % 4:
            raise ValueError("bar start must align to UTC four-hour boundary")
        if prev is not None and ts <= prev:
            raise ValueError("bars must be strictly increasing and unique")
        prev = ts
        if not (isfinite(bar.open) and isfinite(bar.high) and isfinite(bar.low)
                and isfinite(bar.close)):
            return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "invalid_prices")
        if ts + STEP <= cutoff:
            visible.append(bar)
            if directional_states is not None:
                visible_states.append(directional_states[i])
    if len(visible) < REQUIRED_BARS:
        return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "insufficient_history")
    window = visible[-REQUIRED_BARS:]
    times = [b.timestamp.astimezone(timezone.utc) for b in window]
    if any(b - a != STEP for a, b in zip(times, times[1:])):
        return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "missing_4h_candle")
    if times[-1] + STEP > cutoff:
        raise AssertionError("future candle passed causal selection")
    if times[-1] + STEP + STEP <= cutoff:
        return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "stale_data")
    closes = [b.close for b in window]
    if any(p <= 0 for p in closes):
        return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "invalid_prices")
    path = sum(abs(y - x) for x, y in zip(closes[-43:], closes[-42:]))
    efficiency = abs(closes[-1] - closes[-43]) / path if path else 0.0
    returns = [log(y / x) for x, y in zip(closes, closes[1:])]
    long_vol = _population_std(returns)
    if long_vol == 0:
        return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "zero_long_volatility")
    hi = max(b.high for b in visible[-126:])
    lo = min(b.low for b in visible[-126:])
    if hi <= lo:
        return EarlyTrendFeatures(cutoff.isoformat(), "UNAVAILABLE", "zero_price_range")
    age = None
    if directional_states is not None:
        age = 0
        for state in reversed(visible_states):
            if state != direction:
                break
            age += 1
    return EarlyTrendFeatures(
        feature_cutoff_utc=(times[-1] + STEP).isoformat(),
        feature_status="AVAILABLE" if age is not None else "PARTIAL",
        reason="ok" if age is not None else "direction_state_unavailable",
        trend_age_bars=age,
        efficiency_42=efficiency,
        vol_ratio_42_126=_population_std(returns[-42:]) / long_vol,
        range_position_126=(closes[-1] - lo) / (hi - lo),
    )
