from decimal import Decimal

import pytest

from emergenzix_api.paper_position_projection import (
    PositionFill,
    empty_position,
    project_fill,
)


def fill(fill_id: str, side: str, quantity: str, price: str) -> PositionFill:
    return PositionFill(fill_id, "BTCUSDT", side, Decimal(quantity), Decimal(price))  # type: ignore[arg-type]


def test_buys_create_weighted_average_position_and_are_idempotent():
    position = project_fill(empty_position("BTCUSDT"), fill("f1", "BUY", "1", "100"))
    position = project_fill(position, fill("f2", "BUY", "3", "120"))
    assert position.quantity == Decimal("4")
    assert position.average_cost == Decimal("115")
    assert project_fill(position, fill("f2", "BUY", "3", "120")) == position


def test_partial_sell_keeps_cost_basis_and_realizes_pnl():
    bought = project_fill(empty_position("BTCUSDT"), fill("f1", "BUY", "4", "100"))
    sold = project_fill(bought, fill("f2", "SELL", "1.5", "120"))
    assert sold.quantity == Decimal("2.5")
    assert sold.average_cost == Decimal("100")
    assert sold.realized_pnl == Decimal("30.0")


def test_full_sell_closes_position_without_losing_realized_pnl():
    bought = project_fill(empty_position("BTCUSDT"), fill("f1", "BUY", "2", "100"))
    closed = project_fill(bought, fill("f2", "SELL", "2", "90"))
    assert closed.quantity == 0
    assert closed.average_cost == 0
    assert closed.realized_pnl == Decimal("-20")


def test_oversell_symbol_mismatch_and_invalid_values_fail_closed():
    bought = project_fill(empty_position("BTCUSDT"), fill("f1", "BUY", "1", "100"))
    with pytest.raises(ValueError, match="exceeds"):
        project_fill(bought, fill("f2", "SELL", "2", "100"))
    with pytest.raises(ValueError, match="matching symbol"):
        project_fill(
            bought,
            PositionFill("f3", "ETHUSDT", "BUY", Decimal("1"), Decimal("100")),
        )
    with pytest.raises(ValueError, match="positive and finite"):
        project_fill(bought, fill("f4", "BUY", "NaN", "100"))
