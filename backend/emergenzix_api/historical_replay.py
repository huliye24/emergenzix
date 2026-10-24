"""Point-in-time historical replay experiments with a strictly hidden future."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .scenarios import analyze_scenarios
from .schemas import Bar


REPLAY_PROTOCOL_VERSION = "btc-point-in-time-v2"


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ReplayExperiment:
    """One reproducible scenario decision and its separately revealed outcome."""

    seed: int | None
    anchor_at: str
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
            "anchor_at": self.anchor_at,
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
    bars: Sequence[Bar],
    *,
    seed: int | None = None,
    anchor_at: datetime | None = None,
    horizon_bars: int = 12,
    lookback: int = 20,
) -> ReplayExperiment:
    """Replay from either a reproducible random seed or an exact candle timestamp."""
    if (seed is None) == (anchor_at is None):
        raise ValueError("provide exactly one of seed or anchor_at")
    if seed is not None and seed < 0:
        raise ValueError("seed must be non-negative")
    if anchor_at is not None and (
        anchor_at.tzinfo is None or anchor_at.utcoffset() is None
    ):
        raise ValueError("anchor_at must include a timezone offset")
    if horizon_bars < 1:
        raise ValueError("horizon_bars must be positive")
    if lookback < 10:
        raise ValueError("lookback must be at least 10")

    minimum_observed = lookback + 4
    maximum_anchor = len(bars) - horizon_bars - 1
    minimum_anchor = minimum_observed - 1
    if maximum_anchor < minimum_anchor:
        raise ValueError(
            "dataset is too short for the requested lookback and outcome horizon"
        )

    timestamps = [bar.timestamp.astimezone(timezone.utc) for bar in bars]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("bars must be in strictly increasing timestamp order")

    if anchor_at is not None:
        normalized_anchor = anchor_at.astimezone(timezone.utc)
        try:
            anchor_index = timestamps.index(normalized_anchor)
        except ValueError as exc:
            raise ValueError(
                "anchor_at must exactly match a candle open timestamp in the dataset"
            ) from exc
        if anchor_index < minimum_anchor or anchor_index > maximum_anchor:
            raise ValueError(
                "anchor_at does not leave enough past lookback and future outcome bars"
            )
    else:
        # The branch above guarantees seed is an integer here.
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
        "maximum_favorable_move_pct": round(
            (max(bar.high for bar in hidden) / anchor_close - 1.0) * 100.0, 6
        ),
        "maximum_adverse_move_pct": round(
            (min(bar.low for bar in hidden) / anchor_close - 1.0) * 100.0, 6
        ),
    }
    anchor_timestamp = timestamps[anchor_index]
    return ReplayExperiment(
        seed=seed,
        anchor_at=_utc_iso(anchor_timestamp),
        anchor_index=anchor_index,
        observed_bars_count=len(observed),
        horizon_bars=horizon_bars,
        observed_through=_utc_iso(timestamps[anchor_index]),
        outcome_start=_utc_iso(timestamps[anchor_index + 1]),
        outcome_end=_utc_iso(timestamps[anchor_index + horizon_bars]),
        scenario_at_anchor=scenario.as_dict(),
        outcome=outcome,
    )
