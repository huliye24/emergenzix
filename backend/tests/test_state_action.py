"""STATE/ACTION facade must preserve 4M fail-closed decisions."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from emergenzix_api.four_m.engine import Permission, Snapshot, Trend
from emergenzix_api.four_m.state_action import evaluate


def _sample():
    return Snapshot(
        as_of=datetime(2026, 10, 28, tzinfo=timezone.utc),
        macro_votes=(1, 1, 0, 1),
        market_trend=1,
        valuation=0,
        holder_flow=1,
        etf_flow=1,
        price_trend=Trend.UP,
        data_fresh=True,
        risk_ok=True,
    )


def test_two_concepts_are_observable_but_cannot_place_orders():
    result = evaluate(_sample())
    assert result.state.regime.value == "expansion"
    assert result.action.recommendation == Permission.REVIEW_LONG
    assert result.action.mode == "PAPER_RESEARCH"
    assert result.action.live_trading_enabled is False
    assert not hasattr(result.action, "submit_order")
    with pytest.raises(FrozenInstanceError):
        result.action.mode = "LIVE"


@pytest.mark.parametrize("change,expected", [
    ({"data_fresh": False}, Permission.HALT),
    ({"risk_ok": False}, Permission.HALT),
    ({"macro_votes": (None, 1, 1, 1)}, Permission.WAIT),
    ({"price_trend": Trend.RANGE}, Permission.WAIT),
])
def test_fail_closed_against_missing_or_unsafe_evidence(change, expected):
    result = evaluate(replace(_sample(), **change))
    assert result.action.recommendation == expected
    assert result.action.live_trading_enabled is False


def test_deterministic_no_side_effects():
    snap = _sample()
    assert evaluate(snap) == evaluate(snap)
