from emergenzix_api.parameter_stability import (
    RETEST_ZONE_PARAMETER_AXES,
    ParameterEvaluation,
    parameter_grid,
    select_stable_parameters,
)


def evaluation(value: int, score: float) -> ParameterEvaluation:
    return ParameterEvaluation({"threshold": value}, score)


def test_requested_retest_grid_contains_1600_combinations() -> None:
    grid = parameter_grid(RETEST_ZONE_PARAMETER_AXES)
    assert len(grid) == 5 * 4 * 5 * 4 * 4 == 1600
    assert {
        "atr_lower": 0.25,
        "atr_upper": 0.15,
        "score_threshold": 75,
        "confirmation_bars": 2,
        "risk_fraction": 0.03,
    } in grid


def test_isolated_profit_peak_loses_to_stable_plateau() -> None:
    axes = {"threshold": (60, 65, 70, 75, 80, 85, 90)}
    result = select_stable_parameters([
        evaluation(60, -2), evaluation(65, 100), evaluation(70, -1),
        evaluation(75, 8), evaluation(80, 9), evaluation(85, 8), evaluation(90, -1),
    ], axes=axes, minimum_neighbors=2, minimum_profitable_fraction=0.5)
    assert result.parameters["threshold"] == 80
    assert result.diagnostics["stable_plateau"] is True
    assert result.diagnostics["selected_score"] == 9
    assert result.diagnostics["raw_best_score"] == 100
    assert result.diagnostics["in_sample_score_sacrificed_for_stability"] == 91
    assert len(result.diagnostics["parameter_surface"]) == 7
    assert sum(point["parameter_cliff"] for point in result.diagnostics["parameter_surface"]) >= 1


def test_reports_parameter_cliff_when_no_plateau_exists() -> None:
    axes = {"threshold": (70, 75, 80)}
    result = select_stable_parameters([
        evaluation(70, -3), evaluation(75, 20), evaluation(80, -4),
    ], axes=axes, minimum_neighbors=2, minimum_profitable_fraction=0.6)
    assert result.parameters["threshold"] == 75
    assert result.diagnostics["selection_status"] == "no_stable_plateau"
    assert result.diagnostics["parameter_cliff"] is True


def test_rejects_parameters_outside_declared_axes() -> None:
    try:
        select_stable_parameters([evaluation(99, 1)], axes={"threshold": (70, 75)})
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("expected invalid grid value to fail closed")


def test_single_candidate_has_json_safe_no_plateau_diagnostics() -> None:
    result = select_stable_parameters(
        [evaluation(75, 3)], axes={"threshold": (75,)}, minimum_neighbors=1
    )
    assert result.diagnostics["selection_status"] == "no_stable_plateau"
    assert result.diagnostics["neighbor_median_score"] is None
