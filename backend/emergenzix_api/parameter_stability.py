"""Parameter-grid stability diagnostics and plateau-first selection."""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


RETEST_ZONE_PARAMETER_AXES: dict[str, tuple[float | int, ...]] = {
    "atr_lower": (0.15, 0.20, 0.25, 0.30, 0.35),
    "atr_upper": (0.05, 0.10, 0.15, 0.20),
    "score_threshold": (65, 70, 75, 80, 85),
    "confirmation_bars": (1, 2, 3, 4),
    "risk_fraction": (0.02, 0.03, 0.04, 0.05),
}


@dataclass(frozen=True)
class ParameterEvaluation:
    parameters: Mapping[str, Any]
    score: float


@dataclass(frozen=True)
class StabilitySelection:
    parameters: Mapping[str, Any]
    diagnostics: Mapping[str, Any]


def parameter_grid(axes: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    if not axes or any(not values for values in axes.values()):
        raise ValueError("parameter axes and their values must be non-empty")
    names = tuple(axes)
    return [dict(zip(names, values)) for values in itertools.product(*(axes[name] for name in names))]


def select_stable_parameters(
    evaluations: Sequence[ParameterEvaluation],
    *,
    axes: Mapping[str, Sequence[Any]],
    minimum_neighbors: int = 2,
    minimum_profitable_fraction: float = 0.60,
) -> StabilitySelection:
    """Prefer a profitable local plateau over an isolated maximum."""
    if not evaluations:
        raise ValueError("at least one parameter evaluation is required")
    if minimum_neighbors < 1 or not 0 <= minimum_profitable_fraction <= 1:
        raise ValueError("invalid stability thresholds")
    names = tuple(axes)
    positions = {name: {value: index for index, value in enumerate(axes[name])} for name in names}
    seen: set[tuple[Any, ...]] = set()
    indexed: list[tuple[ParameterEvaluation, tuple[int, ...]]] = []
    for evaluation in evaluations:
        if set(evaluation.parameters) != set(names):
            raise ValueError("each parameter set must match the declared axes")
        key = tuple(evaluation.parameters[name] for name in names)
        if key in seen:
            raise ValueError("duplicate parameter set")
        seen.add(key)
        try:
            location = tuple(positions[name][evaluation.parameters[name]] for name in names)
        except KeyError as exc:
            raise ValueError("parameter value is outside the declared axes") from exc
        indexed.append((evaluation, location))

    rows: list[dict[str, Any]] = []
    for evaluation, location in indexed:
        neighbors = [
            other for other, other_location in indexed
            if other is not evaluation
            and max(abs(left - right) for left, right in zip(location, other_location)) <= 1
        ]
        neighbor_scores = [float(item.score) for item in neighbors]
        profitable_fraction = (
            sum(score > 0 for score in neighbor_scores) / len(neighbor_scores)
            if neighbor_scores else 0.0
        )
        neighbor_median = statistics.median(neighbor_scores) if neighbor_scores else None
        stable = (
            evaluation.score > 0
            and len(neighbors) >= minimum_neighbors
            and profitable_fraction >= minimum_profitable_fraction
            and neighbor_median is not None
            and neighbor_median > 0
        )
        rows.append({
            "evaluation": evaluation,
            "neighbor_count": len(neighbors),
            "profitable_neighbor_fraction": profitable_fraction,
            "neighbor_median_score": neighbor_median,
            "stable_plateau": stable,
            "parameter_cliff": evaluation.score > 0 and not stable,
        })

    stable_rows = [row for row in rows if row["stable_plateau"]]
    surface = [{
        "parameters": dict(row["evaluation"].parameters),
        "score": float(row["evaluation"].score),
        "neighbor_count": row["neighbor_count"],
        "profitable_neighbor_fraction": row["profitable_neighbor_fraction"],
        "neighbor_median_score": row["neighbor_median_score"],
        "stable_plateau": row["stable_plateau"],
        "parameter_cliff": row["parameter_cliff"],
    } for row in rows]
    raw_best_score = max(float(row["evaluation"].score) for row in rows)
    if stable_rows:
        selected = max(stable_rows, key=lambda row: (
            row["profitable_neighbor_fraction"],
            row["neighbor_median_score"] if row["neighbor_median_score"] is not None else float("-inf"),
            float(row["evaluation"].score),
        ))
    else:
        # Preserve the best observed candidate but label it unsafe for promotion;
        # inventing a losing "plateau" would be more misleading than a cliff.
        selected = max(rows, key=lambda row: float(row["evaluation"].score))
    evaluation = selected.pop("evaluation")
    diagnostics = {
        **selected,
        "selection_status": "stable_plateau" if stable_rows else "no_stable_plateau",
        "selected_score": float(evaluation.score),
        "raw_best_score": raw_best_score,
        "in_sample_score_sacrificed_for_stability": raw_best_score - float(evaluation.score),
        "evaluated_parameter_sets": len(evaluations),
        "stable_parameter_sets": len(stable_rows),
        "parameter_surface": surface,
    }
    return StabilitySelection(dict(evaluation.parameters), diagnostics)
