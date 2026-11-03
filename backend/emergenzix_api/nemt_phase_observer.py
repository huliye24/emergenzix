"""Price-only NEMT phase observation for advisory market monitoring.

The DCI thresholds and phase labels are adapted from
``huliye24/NEMT-Simulator2@8582b66`` (``nemt_signals.py``), whose file header
is Apache-2.0. This reduced adapter deliberately omits position, leverage,
execution, open-interest, funding-rate, on-chain, and resonance instructions.
"""

from __future__ import annotations

from typing import Sequence

from .schemas import Bar


SOURCE_REPOSITORY = "huliye24/NEMT-Simulator2"
SOURCE_COMMIT = "8582b66f3bff4b2aa6d2433399c6d3c4452761c9"
SOURCE_FILE = "nemt_signals.py"
SOURCE_LICENSE = "Apache-2.0"


def observe_nemt_phase(bars: Sequence[Bar], *, periods: int = 24) -> dict[str, object]:
    """Return a stateless price-only DCI phase; never return execution advice."""
    if periods < 2 or len(bars) < periods + 1:
        raise ValueError(f"at least {periods + 1} completed bars are required")
    closes = [float(bar.close) for bar in bars[-(periods + 1) :]]
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    up_count = sum(value > 0 for value in changes)
    down_count = sum(value < 0 for value in changes)
    dci = max(up_count, down_count) / periods
    direction = (
        "BULLISH" if up_count > down_count else "BEARISH" if down_count > up_count else "NEUTRAL"
    )

    if dci > 0.65:
        phase = "D_TREND"
        confidence = min(1.0, (dci - 0.5) * 2.0)
        signal = "WATCH_LONG" if direction == "BULLISH" else "NO_ACTION"
        strategy_candidate = "TURTLE_20_10" if signal == "WATCH_LONG" else None
    else:
        # Price-only OHLCV cannot establish vortex or resonance phases because
        # the upstream definitions require OI, funding, on-chain, or event data.
        phase = "A_NOISE_OR_TRANSITION"
        confidence = 0.5 if dci >= 0.55 else max(0.0, 1.0 - (0.55 - dci) / 0.1)
        signal = "NO_ACTION"
        strategy_candidate = None

    return {
        "observer": "nemt_dci_price_phase_v1",
        "phase": phase,
        "confidence": round(confidence, 6),
        "dci": round(dci, 6),
        "direction": direction,
        "research_signal": signal,
        "strategy_candidate": strategy_candidate,
        "coverage": "price_only_phases_a_or_d",
        "unavailable_phases": ["B_VORTEX", "C_RESONANCE"],
        "order_permission": False,
        "provenance": {
            "repository": SOURCE_REPOSITORY,
            "commit": SOURCE_COMMIT,
            "source_file": SOURCE_FILE,
            "license": SOURCE_LICENSE,
        },
    }
