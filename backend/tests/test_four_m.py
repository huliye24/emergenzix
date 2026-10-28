"""Safety and deterministic state tests for research-only 4M."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta

import pytest

from emergenzix_api.four_m.engine import (
    Assessment, Bias, Permission, Regime, Snapshot, Trend, assess,
)


def sample(**changes):
    base = Snapshot(
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
    return replace(base, **changes)


def test_aligned_long_is_review_only():
    result = assess(sample())
    assert result.macro == Bias.POSITIVE
    assert result.regime == Regime.EXPANSION
    assert result.permission == Permission.REVIEW_LONG
    assert result.mode == "PAPER_RESEARCH"
    assert result.live_trading_enabled is False


def test_aligned_short_is_review_only():
    result = assess(sample(macro_votes=(-1, -1, 0, -1), market_trend=-1,
                           holder_flow=-1, etf_flow=-1, price_trend=Trend.DOWN))
    assert result.permission == Permission.REVIEW_SHORT


@pytest.mark.parametrize("change", [
    {"macro_votes": (None, 1, 1, 1)},
    {"holder_flow": None},
    {"price_trend": Trend.UNKNOWN},
    {"price_trend": Trend.RANGE},
    {"macro_votes": (-1, -1, -1, -1)},
])
def test_missing_or_conflicting_evidence_never_opens(change):
    assert assess(sample(**change)).permission == Permission.WAIT


@pytest.mark.parametrize("change", [
    {"data_fresh": False}, {"risk_ok": False, "risk_reason": "drawdown_limit"},
])
def test_risk_and_freshness_veto(change):
    result = assess(sample(**change))
    assert result.permission == Permission.HALT


def test_invalid_votes_and_dates_rejected():
    with pytest.raises(ValueError):
        assess(sample(macro_votes=(2, 1, 1, 1)))
    with pytest.raises(ValueError):
        assess(sample(macro_votes=(True, 1, 1, 1)))
    with pytest.raises(ValueError):
        assess(sample(as_of=datetime(2026, 10, 28)))
    with pytest.raises(ValueError):
        assess(sample(as_of=datetime.now(timezone.utc) + timedelta(days=1)))


def test_deterministic_and_immutable():
    snapshot = sample()
    assert assess(snapshot) == assess(snapshot)
    assert isinstance(assess(snapshot), Assessment)
