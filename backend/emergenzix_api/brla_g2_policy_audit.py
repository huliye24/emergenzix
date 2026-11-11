"""Read-only probability diagnostics for a frozen BRLA-G2 PPO policy."""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np

PROTOCOL = "emergenzix-brla-g2-policy-probability-audit-v1"


def summarize_flat_probabilities(rows: Sequence[Mapping[str, float]]) -> dict[str, float | int]:
    if not rows:
        raise ValueError("at least one flat-state probability row is required")
    wait = np.asarray([float(row["wait_probability"]) for row in rows])
    probe = np.asarray([float(row["probe_probability"]) for row in rows])
    if any(not math.isfinite(value) or not 0 <= value <= 1
           for value in np.concatenate((wait, probe))):
        raise ValueError("action probabilities must be finite and in [0, 1]")
    margins = wait - probe
    return {
        "flat_decision_count": len(rows),
        "mean_wait_probability": float(wait.mean()),
        "mean_probe_probability": float(probe.mean()),
        "median_wait_minus_probe_margin": float(np.median(margins)),
        "probe_argmax_fraction": float((probe > wait).mean()),
        "decisive_wait_fraction": float((margins >= .10).mean()),
        "near_tie_fraction": float((np.abs(margins) < .05).mean()),
    }
