"""Point-in-time historical replay experiments with a strictly hidden future."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from .scenarios import analyze_scenarios
from .schemas import Bar


REPLAY_PROTOCOL_VERSION = "btc-point-in-time-v1"


@dataclass(frozen=True)
class ReplayExperiment:
    """One reproducible scenario decision and its separately revealed outcome."""

    seed: int
    anchor_index: int
    observed_bars_count: int
    horizon_bars: int
    observed_through: str
    outcome_start: str
    outcome_end: str
    scenario_at_anchor: dict[str, object]
    outcome: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "anchor_index": self.anchor_index,
            "observed_bars_count": self.observed_bars_count,
            "horizon_bars": self.horizon_bars,
            "observed_through": self.observed_through,
            "outcome_start": self.outcome_start,
            "outcome_end": self.outcome_end,
            "scenario_at_anchor": self.scenario_at_anchor,
            "outcome": self.outcome,
            "safety": {
                "point_in_time": True,
                "future_visible_during_analysis": False,
                "orders_created": False,
            },
        }


def run_historical_replay(
    bars: Sequence[Bar], *, seed: int, horizon_bars: int = 12, lookback: int = 20
) -> ReplayExperiment:
    """Choose a reproducible anchor, analyze only its past, then score hidden bars."""
    if horizon_bars < 1:
        raise ValueError("horizon_bars must be positive")
    minimum_observed = lookback + 4
    maximum_anchor = len(bars) - horizon_bars - 1
    minimum_anchor = minimum_observed - 1
    if maximum_anchor < minimum_anchor:
        raise ValueError("dataset is too short for the requested lookback and outcome horizon")
    timestamps = [bar.timestamp for bar in bars]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("bars must be in strictly increasing timestamp order")

    anchor_index = random.Random(seed).randint(minimum_anchor, maximum_anchor)
    observed = bars[: anchor_index + 1]
    hidden = bars[anchor_index + 1 : anchor_index + 1 + horizon_bars]
    scenario = analyze_scenarios(observed, lookback=lookback)

    anchor_close = observed[-1].close
    closes = [bar.close for bar in hidden]
    support = scenario.support
    resistance = scenario.resistance
    if closes[-1] < support and min(closes) < support:
        realized_scenario = "continuation_down"
    elif min(bar.low for bar in hidden) < support and closes[-1] >= support:
        realized_scenario = "false_breakdown"
    elif closes[-1] > resistance:
        realized_scenario = "reversal_up"
    else:
        realized_scenario = "range"

    outcome = {
        "realized_scenario": realized_scenario,
        "scenario_match": realized_scenario == scenario.scenario,
        "anchor_close": round(anchor_close, 8),
        "final_close": round(closes[-1], 8),
        "return_pct": round((closes[-1] / anchor_close - 1.0) * 100.0, 6),
        "maximum_favorable_move_pct": round((max(bar.high for bar in hidden) / anchor_close - 1.0) * 100.0, 6),
        "maximum_adverse_move_pct": round((min(bar.low for bar in hidden) / anchor_close - 1.0) * 100.0, 6),
    }
    return ReplayExperiment(
        seed=seed,
        anchor_index=anchor_index,
        observed_bars_count=len(observed),
        horizon_bars=horizon_bars,
        observed_through=observed[-1].timestamp.isoformat(),
        outcome_start=hidden[0].timestamp.isoformat(),
        outcome_end=hidden[-1].timestamp.isoformat(),
        scenario_at_anchor=scenario.as_dict(),
        outcome=outcome,
    )
