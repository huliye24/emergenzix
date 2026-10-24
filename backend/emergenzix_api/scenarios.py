"""Deterministic market-scenario classification from completed OHLCV bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .schemas import Bar


SCENARIOS = (
    "continuation_down",
    "false_breakdown",
    "range",
    "reversal_up",
)


@dataclass(frozen=True)
class ScenarioResult:
    """A reviewable scenario snapshot; probabilities are heuristic, not forecasts."""

    scenario: str
    confidence: float
    probabilities: dict[str, float]
    support: float
    resistance: float
    atr: float
    trigger: str
    invalidation: str
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "key_levels": {
                "support": self.support,
                "resistance": self.resistance,
                "atr": self.atr,
            },
            "trigger": self.trigger,
            "invalidation": self.invalidation,
            "limitations": list(self.limitations),
        }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _average_true_range(bars: Sequence[Bar], period: int = 14) -> float:
    selected = bars[-(period + 1) :]
    ranges: list[float] = []
    for previous, current in zip(selected, selected[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return _mean(ranges)


def analyze_scenarios(
    bars: Sequence[Bar], *, lookback: int = 20
) -> ScenarioResult:
    """Classify the latest completed bars without reading any future observations."""
    minimum_bars = lookback + 4
    if len(bars) < minimum_bars:
        raise ValueError(f"at least {minimum_bars} completed bars are required")
    if lookback < 10:
        raise ValueError("lookback must be at least 10")

    reference = bars[-(lookback + 3) : -3]
    recent = bars[-3:]
    closes = [bar.close for bar in bars]
    support = min(bar.low for bar in reference)
    resistance = max(bar.high for bar in reference)
    atr = _average_true_range(bars)
    if atr <= 0:
        raise ValueError("ATR must be positive")

    current = recent[-1]
    previous = recent[-2]
    fast_mean = _mean(closes[-5:])
    slow_mean = _mean(closes[-20:])
    average_volume = _mean([bar.volume for bar in reference])
    volume_ratio = current.volume / average_volume if average_volume > 0 else 1.0
    buffer = max(atr * 0.15, current.close * 0.001)

    confirmed_down = previous.close < support - buffer and current.close < support - buffer
    breached_support = min(bar.low for bar in recent) < support - buffer
    reclaimed_support = current.close > support + buffer
    confirmed_up = previous.close > resistance + buffer and current.close > resistance + buffer

    scores = {
        "continuation_down": 1.0
        + max(0.0, (support - current.close) / atr) * 2.0
        + max(0.0, (slow_mean - fast_mean) / atr)
        + (2.5 if confirmed_down else 0.0)
        + max(0.0, volume_ratio - 1.0) * 0.5,
        "false_breakdown": 1.0
        + (4.0 if breached_support and reclaimed_support else 0.0)
        + max(0.0, (current.close - current.open) / atr),
        "range": 1.0
        + (2.5 if support <= current.close <= resistance else 0.0)
        + max(0.0, 1.0 - abs(fast_mean - slow_mean) / atr),
        "reversal_up": 1.0
        + max(0.0, (current.close - resistance) / atr) * 2.0
        + max(0.0, (fast_mean - slow_mean) / atr)
        + (2.5 if confirmed_up else 0.0)
        + max(0.0, volume_ratio - 1.0) * 0.5,
    }
    total = sum(scores.values())
    probabilities = {name: round(score / total, 6) for name, score in scores.items()}
    scenario = max(SCENARIOS, key=lambda name: probabilities[name])

    instructions = {
        "continuation_down": (
            f"two completed closes below {support:.8g}",
            f"completed close back above {support + buffer:.8g}",
        ),
        "false_breakdown": (
            f"support sweep followed by close above {support + buffer:.8g}",
            f"completed close below {support - buffer:.8g}",
        ),
        "range": (
            f"price remains between {support:.8g} and {resistance:.8g}",
            "two completed closes outside the range buffer",
        ),
        "reversal_up": (
            f"two completed closes above {resistance:.8g}",
            f"completed close back below {resistance - buffer:.8g}",
        ),
    }
    trigger, invalidation = instructions[scenario]
    return ScenarioResult(
        scenario=scenario,
        confidence=probabilities[scenario],
        probabilities=probabilities,
        support=round(support, 8),
        resistance=round(resistance, 8),
        atr=round(atr, 8),
        trigger=trigger,
        invalidation=invalidation,
        limitations=(
            "Heuristic scenario weights are not calibrated forecast probabilities.",
            "Only completed, correctly ordered candles should be supplied.",
            "No order is created by scenario analysis.",
        ),
    )
