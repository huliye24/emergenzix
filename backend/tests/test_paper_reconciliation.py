from decimal import Decimal

from emergenzix_api.paper_reconciliation import (
    ReconciliationState,
    reconcile_paper_state,
)


def state(*, cash="1000", positions=None, orders=None):
    return ReconciliationState(
        cash=Decimal(cash),
        positions=positions or {"BTCUSDT": Decimal("1")},
        open_orders=orders or {"order-1": "ACKNOWLEDGED"},
    )


def test_exact_match_does_not_freeze_account():
    result = reconcile_paper_state(state(), state())
    assert result.matched is True
    assert result.freeze_account is False
    assert result.discrepancies == ()


def test_cash_position_and_order_differences_are_all_reported():
    local = state()
    executor = state(
        cash="999",
        positions={"BTCUSDT": Decimal("0.5"), "ETHUSDT": Decimal("2")},
        orders={"order-2": "ACKNOWLEDGED"},
    )
    result = reconcile_paper_state(local, executor)
    assert result.freeze_account is True
    assert result.discrepancies == (
        "cash_mismatch",
        "position_mismatch:BTCUSDT",
        "position_mismatch:ETHUSDT",
        "order_mismatch:order-1",
        "order_mismatch:order-2",
    )


def test_non_finite_values_fail_closed():
    result = reconcile_paper_state(state(cash="NaN"), state())
    assert result.freeze_account is True
    assert result.discrepancies == ("cash_non_finite",)
