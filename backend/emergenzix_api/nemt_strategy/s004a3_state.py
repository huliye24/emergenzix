"""S-004.A.3 pinned per-bar causal direction state (research only).

The A.3 protocol requires ``trend_age_bars`` to come from "the pinned candidate
engine", not from a re-derivation.  This module is the single source of truth:
it exports, for every completed 4h bar, the direction state the candidate engine
itself uses to open trades.

Definition (deliberately identical to the engine):
    state[i] = _direction(compute_momentum_state(closes[:i+1]))
    "LONG" if +1, "SHORT" if -1, "NEUTRAL" if 0

It never looks past bar ``i``; no future values are used.
"""

from __future__ import annotations

from typing import Sequence

from ..schemas import Bar
from .nemt_momentum_v2 import MomentumV2Config, compute_momentum_state
from .v522_ablation_cli import _direction

MIN_CLOSES = 5          # compute_momentum_state needs five closes


def causal_direction_states(bars: Sequence[Bar]) -> list[str]:
    """Return one LONG/SHORT/NEUTRAL label per input bar, strictly causal.

    ``compute_momentum_state`` only reads the trailing ``window`` closes, so
    passing that window is exactly equivalent to passing the whole prefix while
    avoiding an O(n) slice on every bar.  ``test_s004a3_state`` asserts the
    equivalence against the full-prefix call.
    """
    window = MomentumV2Config().window
    closes: list[float] = []
    states: list[str] = []
    for bar in bars:
        closes.append(bar.close)
        if len(closes) < MIN_CLOSES:
            states.append("NEUTRAL")
            continue
        trailing = closes[-window:]
        direction = _direction(compute_momentum_state(trailing))
        states.append("LONG" if direction == 1 else ("SHORT" if direction == -1 else "NEUTRAL"))
    return states


def trend_age(states: Sequence[str], index: int, direction: str) -> int:
    """Completed bars since the state last agreed with `direction` (0 = just flipped)."""
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    age = 0
    for cursor in range(index, -1, -1):
        if states[cursor] != direction:
            break
        age += 1
    return age
