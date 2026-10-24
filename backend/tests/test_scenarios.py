from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.scenarios import analyze_scenarios
from emergenzix_api.schemas import Bar


def _bars(closes: list[float]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=start + timedelta(hours=4 * index),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1_000,
        )
        for index, close in enumerate(closes)
    ]


def test_confirmed_breakdown_is_classified_without_future_data() -> None:
    bars = _bars([100 + (index % 3) for index in range(27)] + [98, 96, 94])
    result = analyze_scenarios(bars)
    assert result.scenario == "continuation_down"
    assert result.probabilities["continuation_down"] == result.confidence
    assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=0.00001)


def test_support_sweep_and_reclaim_is_false_breakdown() -> None:
    bars = _bars([100 + (index % 3) for index in range(27)] + [98, 99, 102])
    bars[-3] = bars[-3].model_copy(update={"low": 95.0})
    result = analyze_scenarios(bars)
    assert result.scenario == "false_breakdown"


def test_range_is_default_when_no_breakout_is_confirmed() -> None:
    result = analyze_scenarios(_bars([100 + (index % 3) for index in range(30)]))
    assert result.scenario == "range"


def test_insufficient_history_fails_closed() -> None:
    with pytest.raises(ValueError, match="completed bars"):
        analyze_scenarios(_bars([100.0] * 23))


def test_scenario_endpoint_is_analysis_only(client) -> None:
    payload = {
        "bars": [bar.model_dump(mode="json") for bar in _bars([100 + (i % 3) for i in range(30)])],
        "lookback": 20,
    }
    response = client.post("/api/v1/scenarios/analyze", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["scenario"] == "range"
    assert "order" not in response.json()
