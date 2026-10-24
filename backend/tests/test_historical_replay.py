from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.historical_replay import run_historical_replay
from emergenzix_api.schemas import Bar


def _bars(count: int = 100) -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=start + timedelta(hours=4 * index),
            open=80_000 + index * 20,
            high=80_200 + index * 20,
            low=79_800 + index * 20,
            close=80_100 + index * 20,
            volume=1_000 + index,
        )
        for index in range(count)
    ]


def test_replay_is_reproducible_and_never_creates_orders() -> None:
    first = run_historical_replay(_bars(), seed=42, horizon_bars=12).as_dict()
    second = run_historical_replay(_bars(), seed=42, horizon_bars=12).as_dict()
    assert first == second
    assert first["safety"] == {
        "point_in_time": True,
        "future_visible_during_analysis": False,
        "orders_created": False,
    }


def test_future_changes_cannot_change_anchor_analysis() -> None:
    original = _bars()
    baseline = run_historical_replay(original, seed=7, horizon_bars=10)
    changed = list(original)
    for index in range(baseline.anchor_index + 1, len(changed)):
        changed[index] = changed[index].model_copy(
            update={"open": 40_000.0, "high": 41_000.0, "low": 39_000.0, "close": 40_000.0}
        )
    replayed = run_historical_replay(changed, seed=7, horizon_bars=10)
    assert replayed.anchor_index == baseline.anchor_index
    assert replayed.scenario_at_anchor == baseline.scenario_at_anchor
    assert replayed.outcome != baseline.outcome


def test_replay_rejects_insufficient_history() -> None:
    with pytest.raises(ValueError, match="too short"):
        run_historical_replay(_bars(30), seed=1, horizon_bars=12, lookback=20)
