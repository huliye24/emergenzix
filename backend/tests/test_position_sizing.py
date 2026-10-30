"""Deterministic PAPER sizing tests; no database, API, or order side effects."""

from __future__ import annotations

import math

import pytest

from emergenzix_api.position_sizing import (
    LongPaperSizingRequest,
    size_long_paper_position,
)


def request(**overrides: float) -> LongPaperSizingRequest:
    values = {
        "equity": 10_000.0,
        "available_cash": 10_000.0,
        "entry_price": 100.0,
        "protective_stop": 95.0,
        "risk_fraction": 0.01,
        "max_notional": 20_000.0,
        "fee_bps": 10.0,
        "slippage_bps": 20.0,
    }
    values.update(overrides)
    return LongPaperSizingRequest(**values)


def test_stop_risk_budget_binds_and_includes_cost_buffer() -> None:
    decision = size_long_paper_position(request())

    assert decision.executable
    assert decision.binding_cap == "risk_budget"
    assert decision.modeled_entry_fill > 100.0
    assert decision.modeled_stop_fill < 95.0
    assert decision.risk_per_unit > 5.0
    assert decision.estimated_loss_at_stop == pytest.approx(decision.risk_budget)
    assert decision.notional <= 20_000.0


def test_cash_and_notional_caps_are_reported() -> None:
    cash_limited = size_long_paper_position(request(available_cash=500.0))
    notional_limited = size_long_paper_position(request(max_notional=400.0))

    assert cash_limited.binding_cap == "available_cash"
    assert cash_limited.notional < 500.0
    assert notional_limited.binding_cap == "max_notional"
    assert notional_limited.notional == pytest.approx(400.0)
    assert cash_limited.estimated_loss_at_stop <= cash_limited.risk_budget
    assert notional_limited.estimated_loss_at_stop <= notional_limited.risk_budget


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"equity": 0.0}, "equity"),
        ({"available_cash": 0.0}, "cash"),
        ({"entry_price": 0.0}, "prices"),
        ({"protective_stop": 100.0}, "below entry"),
        ({"protective_stop": 101.0}, "below entry"),
        ({"risk_fraction": 0.0}, "risk fraction"),
        ({"risk_fraction": 0.11}, "risk fraction"),
        ({"max_notional": 0.0}, "notional"),
        ({"fee_bps": -1.0}, "fee"),
        ({"slippage_bps": 10_000.0}, "slippage"),
        ({"entry_price": math.nan}, "finite"),
        ({"equity": math.inf}, "finite"),
    ],
)
def test_unsafe_inputs_fail_closed(overrides: dict[str, float], reason: str) -> None:
    decision = size_long_paper_position(request(**overrides))

    assert not decision.executable
    assert decision.quantity == 0.0
    assert decision.binding_cap == "rejected"
    assert reason in (decision.rejection_reason or "")
