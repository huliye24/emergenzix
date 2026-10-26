"""System-layer settled-equity invariants without markets, APIs or orders."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from emergenzix_api.equity_ledger import SettledEquityLedger


def test_100_to_120_to_132_rolls_all_net_profit_into_next_budget() -> None:
    first = SettledEquityLedger.start("100")
    second = first.settle_trade(
        trade_id="closed-1", gross_pnl="20.70",
        entry_fee="0.50", exit_fee="0.20",
    )
    assert first.settled_cash == Decimal("100")
    assert second.settled_cash == Decimal("120.00")
    assert second.budget("0.01") == Decimal("1.2000")
    third = second.settle_trade(trade_id="closed-2", gross_pnl="12")
    assert third.settled_cash == Decimal("132.00")
    assert third.budget("0.01") == Decimal("1.3200")
    assert third.realized_net == Decimal("32.00")
    assert third.audit()["orders_created"] is False
    assert third.audit()["open_position_mark_to_market_included"] is False


def test_losses_reduce_next_budget_and_insolvency_is_never_hidden() -> None:
    loss = SettledEquityLedger.start(100).settle_trade(
        trade_id="lose", gross_pnl="-20",
    )
    assert loss.settled_cash == Decimal("80")
    assert loss.budget("0.01") == Decimal("0.80")
    bankrupt = loss.settle_trade(trade_id="gap-stop", gross_pnl="-100")
    assert bankrupt.settled_cash == Decimal("-20")
    assert bankrupt.budget("0.01") == 0
    assert bankrupt.is_insolvent


def test_deposit_changes_equity_but_never_trading_profit() -> None:
    ledger = SettledEquityLedger.start("100").transfer(
        transfer_id="deposit-1", amount="50"
    ).settle_trade(trade_id="win", gross_pnl="20")
    assert ledger.settled_cash == Decimal("170")
    assert ledger.realized_net == Decimal("20")
    assert ledger.external_net == Decimal("50")
    assert ledger.audit()["external_net"] == "50"
    withdrew = ledger.transfer(transfer_id="withdraw-1", amount="-30")
    assert withdrew.settled_cash == Decimal("140")
    assert withdrew.external_net == Decimal("20")
    assert withdrew.realized_net == Decimal("20")


def test_duplicate_trade_and_transfer_are_rejected_without_mutation() -> None:
    initial = SettledEquityLedger.start("100")
    first = initial.settle_trade(trade_id="id1", gross_pnl="2")
    with pytest.raises(ValueError, match="already settled"):
        first.settle_trade(trade_id="id1", gross_pnl="2")
    moved = first.transfer(transfer_id="movement-1", amount="3")
    with pytest.raises(ValueError, match="already booked"):
        moved.transfer(transfer_id="movement-1", amount="3")
    assert first.settled_cash == Decimal("102")
    assert moved.settled_cash == Decimal("105")


def test_costs_are_booked_once_and_decimal_precision_is_exact() -> None:
    l = SettledEquityLedger.start("100.00").settle_trade(
        trade_id="fees", gross_pnl="0.30", entry_fee="0.10",
        exit_fee="0.10", other_cost="0.05",
    )
    assert l.settled_cash == Decimal("100.05")
    assert l.accumulated_costs == Decimal("0.25")
    assert l.realized_net == Decimal("0.05")
    assert l.audit()["settled_cash"] == "100.05"


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", True, float("inf")])
def test_reject_invalid_start_cash(bad) -> None:
    with pytest.raises(ValueError):
        SettledEquityLedger.start(bad)


@pytest.mark.parametrize("bad", ["NaN", "-Infinity", True, None, "abc"])
def test_reject_nonfinite_or_ambiguous_trade_cashflows(bad) -> None:
    with pytest.raises(ValueError):
        SettledEquityLedger.start("100").settle_trade(
            trade_id="t", gross_pnl=bad
        )


@pytest.mark.parametrize("bad", ["-1", "-0.001", "Infinity", "NaN"])
def test_reject_negative_or_invalid_fees(bad) -> None:
    with pytest.raises(ValueError):
        SettledEquityLedger.start("100").settle_trade(
            trade_id="t", gross_pnl=1, exit_fee=bad
        )


def test_reject_over_withdrawal_and_nonfinite_transfer() -> None:
    l = SettledEquityLedger.start("100")
    with pytest.raises(ValueError, match="exceeds"):
        l.transfer(transfer_id="out", amount="-101")
    with pytest.raises(ValueError):
        l.transfer(transfer_id="out", amount="NaN")


def test_reject_invalid_budget_and_trade_ids() -> None:
    l = SettledEquityLedger.start("100")
    for fraction in ("-0.1", "1.1", "NaN", True):
        with pytest.raises(ValueError):
            l.budget(fraction)
    for trade_id in ("", " "*3, "x"*129):
        with pytest.raises(ValueError):
            l.settle_trade(trade_id=trade_id, gross_pnl=1)


def test_snapshot_is_immutable_and_audit_checks_conservation() -> None:
    l = SettledEquityLedger.start(100)
    with pytest.raises(FrozenInstanceError):
        l.settled_cash = Decimal("500")
    from dataclasses import replace
    corrupted = replace(l, settled_cash=Decimal("200"))
    with pytest.raises(ValueError, match="conservation"):
        corrupted.audit()
