from decimal import Decimal

import pytest

from emergenzix_api.paper_accounting import journal_fill


def test_buy_fill_balances_inventory_fee_and_cash():
    journal = journal_fill(
        transaction_id="txn-1",
        order_id="order-1",
        fill_event_id="fill-1",
        side="BUY",
        quantity=Decimal("2"),
        price=Decimal("100"),
        fee=Decimal("0.20"),
    )
    assert journal.total_debits == Decimal("200.20")
    assert journal.total_credits == Decimal("200.20")
    assert journal.realized_pnl == 0


def test_profitable_sell_balances_and_credits_realized_pnl():
    journal = journal_fill(
        transaction_id="txn-2",
        order_id="order-2",
        fill_event_id="fill-2",
        side="SELL",
        quantity=Decimal("2"),
        price=Decimal("120"),
        fee=Decimal("0.24"),
        inventory_unit_cost=Decimal("100"),
    )
    assert journal.balanced
    assert journal.realized_pnl == Decimal("40")
    assert journal.total_debits == Decimal("240")
    assert journal.total_credits == Decimal("240")


def test_losing_sell_debits_realized_pnl_and_still_balances():
    journal = journal_fill(
        transaction_id="txn-3",
        order_id="order-3",
        fill_event_id="fill-3",
        side="SELL",
        quantity=Decimal("1.5"),
        price=Decimal("80"),
        fee=Decimal("0.12"),
        inventory_unit_cost=Decimal("100"),
    )
    assert journal.balanced
    assert journal.realized_pnl == Decimal("-30.0")


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity": Decimal("0")},
        {"price": Decimal("NaN")},
        {"fee": Decimal("-0.01")},
        {"side": "SELL"},
    ],
)
def test_invalid_fill_inputs_fail_closed(overrides):
    values = {
        "transaction_id": "txn",
        "order_id": "order",
        "fill_event_id": "fill",
        "side": "BUY",
        "quantity": Decimal("1"),
        "price": Decimal("100"),
        "fee": Decimal("0"),
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        journal_fill(**values)
