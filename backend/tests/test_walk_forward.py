from datetime import datetime, timezone

import pytest

from emergenzix_api.parameter_stability import select_stable_parameters
from emergenzix_api.walk_forward import WalkForwardProtocol, build_windows, run_walk_forward


UTC = timezone.utc


def dt(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=UTC)


def test_builds_18_3_3_windows_and_rolls_quarterly() -> None:
    windows = build_windows(dt(2023, 1), dt(2025, 7), WalkForwardProtocol())
    assert [(w.train_start, w.selection_start, w.test_start, w.test_end) for w in windows] == [
        (dt(2023, 1), dt(2024, 7), dt(2024, 10), dt(2025, 1)),
        (dt(2023, 4), dt(2024, 10), dt(2025, 1), dt(2025, 4)),
        (dt(2023, 7), dt(2025, 1), dt(2025, 4), dt(2025, 7)),
    ]


def test_anchor_must_be_unambiguous_month_boundary() -> None:
    with pytest.raises(ValueError, match="first day"):
        build_windows(datetime(2023, 1, 2, tzinfo=UTC), dt(2025, 1), WalkForwardProtocol())


def test_future_test_values_cannot_change_selected_parameters() -> None:
    bars = [{"timestamp": dt(2023, month), "value": 1} for month in range(1, 13)]
    bars += [{"timestamp": dt(2024, month), "value": 1} for month in range(1, 13)]
    bars += [{"timestamp": dt(2025, month), "value": 1} for month in range(1, 4)]
    protocol = WalkForwardProtocol(training_months=18, selection_months=3, test_months=3)

    def runner(visible, start, end, params, shared):
        score = params["value"] if end == dt(2024, 10) else sum(
            bar["value"] for bar in visible if start <= bar["timestamp"] < end
        )
        return {"metrics": {"total_return_pct": score, "max_drawdown_pct": 1}, "trades": []}

    arm_ids = ("control", "candidate")
    runners = {name: runner for name in arm_ids}
    candidates = {name: [{"value": 1}, {"value": 2}] for name in arm_ids}
    first = run_walk_forward(
        bars=bars, anchor=dt(2023, 1), data_end=dt(2025, 1), runners=runners,
        candidates=candidates, protocol=protocol,
    )
    for bar in bars:
        if bar["timestamp"] >= dt(2024, 10):
            bar["value"] = 1_000_000
    second = run_walk_forward(
        bars=bars, anchor=dt(2023, 1), data_end=dt(2025, 1), runners=runners,
        candidates=candidates, protocol=protocol,
    )
    assert [r["selected_parameters"] for r in first["windows"]] == [
        r["selected_parameters"] for r in second["windows"]
    ]


def test_arbitrary_arms_share_protocol_and_report_tail_dependence() -> None:
    seen = []
    bars = [{"timestamp": dt(year, month)} for year in (2023, 2024) for month in range(1, 13)]

    def runner(visible, start, end, params, shared):
        seen.append((shared.fee_rate, shared.slippage_rate, shared.risk_fraction, shared.maximum_leverage))
        return {
            "metrics": {"total_return_pct": 3.0, "max_drawdown_pct": 4.0},
            "trades": [{"net_pnl": 10.0}, {"net_pnl": -2.0}, {"net_pnl": 1.0}],
        }

    protocol = WalkForwardProtocol(training_months=12, selection_months=3, test_months=3)
    arm_ids = ("alpha", "beta", "gamma", "replacement")
    report = run_walk_forward(
        bars=bars, anchor=dt(2023, 1), data_end=dt(2024, 7),
        runners={name: runner for name in arm_ids},
        candidates={name: [{"lookback": 20}] for name in arm_ids}, protocol=protocol,
    )
    assert len(set(seen)) == 1
    assert {record["arm"] for record in report["windows"]} == set(arm_ids)
    assert all(record["without_largest_winner"]["remaining_net_pnl"] == -1 for record in report["windows"])
    assert report["execution_mode"] == "BACKTEST_ONLY"
    assert report["live_enabled"] is False


def test_runner_and_candidate_arm_ids_must_match() -> None:
    with pytest.raises(ValueError, match="match exactly"):
        run_walk_forward(
            bars=[], anchor=dt(2023, 1), data_end=dt(2025, 1),
            runners={"alpha": lambda *_: {}}, candidates={"beta": [{}]},
        )


def test_requires_at_least_one_arm() -> None:
    with pytest.raises(ValueError, match="at least one"):
        run_walk_forward(
            bars=[], anchor=dt(2023, 1), data_end=dt(2025, 1), runners={}, candidates={}
        )


def test_stability_selector_uses_selection_period_only() -> None:
    bars = [{"timestamp": dt(year, month), "future": 0} for year in (2023, 2024) for month in range(1, 13)]
    axes = {"threshold": (70, 75, 80)}

    def runner(visible, start, end, params, shared):
        score = {70: 5.0, 75: 6.0, 80: 5.0}[params["threshold"]]
        if start == dt(2024, 10):
            score += sum(bar["future"] for bar in visible if start <= bar["timestamp"] < end)
        return {"metrics": {"total_return_pct": score, "max_drawdown_pct": 1}, "trades": []}

    selector = lambda values: select_stable_parameters(
        values, axes=axes, minimum_neighbors=1, minimum_profitable_fraction=1.0
    )
    kwargs = dict(
        bars=bars, anchor=dt(2023, 1), data_end=dt(2025, 1),
        runners={"any_strategy": runner},
        candidates={"any_strategy": [{"threshold": value} for value in axes["threshold"]]},
        selectors={"any_strategy": selector},
    )
    first = run_walk_forward(**kwargs)
    for bar in bars:
        if bar["timestamp"] >= dt(2024, 10):
            bar["future"] = 1_000_000
    second = run_walk_forward(**kwargs)
    assert first["windows"][0]["selected_parameters"] == second["windows"][0]["selected_parameters"]
    assert first["windows"][0]["selection_diagnostics"]["selection_status"] == "stable_plateau"
