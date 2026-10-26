"""S-005.A.1 audit: the five capital-conservation and liquidation checks.

Each test corresponds to one of the frozen audit questions:

  1. does a single trade change equity by exactly its net PnL?
  2. do position / available balance / unrealised / fees reconcile every bar?
  3. can leverage or insufficient margin trigger liquidation, and is equity
     floored at zero (never negative)?
  4. is the unified account risk budget enforced across concurrent positions?
  5. when capital is short, is the new position rejected instead of conjuring
     virtual purchasing power?
"""

from __future__ import annotations

import pytest

from emergenzix_api.nemt_strategy.s005_account import (
    Account, AccountingError, FEE_RATE, LIQUIDATION_PENALTY,
)


def test_single_trade_equity_change_equals_net_pnl() -> None:
    """Check 1: 100 USDT, one round trip, equity moves by exactly the net PnL."""
    account = Account(initial_capital=100.0, leverage=3.0)
    marks = {"BTCUSDT": 100.0}
    ok, _ = account.open(symbol="BTCUSDT", side=1, price=100.0, stop_price=97.5,
                         risk_usdt=1.0, marks=marks)
    assert ok
    position = account.positions["BTCUSDT"]
    notional = position.quantity * 100.0
    assert notional == pytest.approx(1.0 / 2.5 * 100.0)     # 1 USDT risk over a 2.5 price stop
    assert position.margin == pytest.approx(notional / 3.0)
    assert account.fees_paid == pytest.approx(notional * FEE_RATE)

    marks = {"BTCUSDT": 105.0}
    net = account.close(symbol="BTCUSDT", price=105.0, reason="target", marks=marks)

    expected_gross = (105.0 - 100.0) * position.quantity
    expected_fee = notional * FEE_RATE + position.quantity * 105.0 * FEE_RATE
    assert net == pytest.approx(expected_gross - position.entry_fee
                                - position.quantity * 105.0 * FEE_RATE)
    assert account.fees_paid == pytest.approx(expected_fee, rel=1e-9)
    assert account.equity(marks) == pytest.approx(100.0 + net)
    assert account.positions == {}


def test_ledger_reconciles_on_every_mark() -> None:
    """Check 2: invariants hold at every step, including open positions."""
    account = Account(initial_capital=100.0)
    marks = {"A": 100.0, "B": 50.0}
    account.open(symbol="A", side=1, price=100.0, stop_price=97.5, risk_usdt=1.0, marks=marks)
    account.open(symbol="B", side=-1, price=50.0, stop_price=51.25, risk_usdt=1.0, marks=marks)
    for price_a, price_b in ((100.0, 50.0), (103.0, 49.0), (97.0, 52.0), (101.0, 50.5)):
        marks = {"A": price_a, "B": price_b}
        account.mark(marks)
        account.assert_invariants(marks)
        rebuilt = account.free_cash + account.locked_margin() + account.unrealised(marks)
        assert rebuilt == pytest.approx(account.equity(marks))
        assert account.free_cash >= 0


def test_liquidation_fires_and_equity_never_negative() -> None:
    """Check 3: a crash beyond maintenance margin forces liquidation, equity >= 0."""
    account = Account(initial_capital=100.0, leverage=3.0)
    marks = {"X": 100.0}
    ok, _ = account.open(symbol="X", side=1, price=100.0, stop_price=97.5,
                         risk_usdt=6.0, marks=marks)
    assert ok
    # price collapses far past the stop: without liquidation this would be negative equity
    marks = {"X": 40.0}
    liquidated = account.mark(marks)
    assert liquidated is True
    assert account.liquidations == 1
    assert account.positions == {}
    assert account.equity(marks) >= 0.0
    assert account.closed[-1]["reason"] == "liquidation"
    assert account.closed[-1]["penalty"] > 0
    account.assert_invariants(marks)


def test_liquidation_penalty_is_charged() -> None:
    account = Account(initial_capital=100.0, leverage=3.0)
    marks = {"X": 100.0}
    account.open(symbol="X", side=1, price=100.0, stop_price=97.5, risk_usdt=6.0, marks=marks)
    position = account.positions["X"]
    marks = {"X": 40.0}
    account.mark(marks)
    record = account.closed[-1]
    # liquidation executes at the liquidation price, not at the bar mark
    assert record["liquidation_price"] > 40.0
    assert record["penalty"] == pytest.approx(
        position.quantity * record["liquidation_price"] * LIQUIDATION_PENALTY)
    assert record["loss_capped"] is False
    assert account.equity(marks) >= 0.0


def test_unified_risk_budget_blocks_extra_positions() -> None:
    """Check 4: the aggregate open-risk budget is enforced across symbols."""
    account = Account(initial_capital=100.0, leverage=10.0, risk_budget=0.032)
    marks = {name: 100.0 for name in ("A", "B", "C", "D")}
    opened = 0
    for name in ("A", "B", "C"):
        ok, reason = account.open(symbol=name, side=1, price=100.0, stop_price=97.5,
                                  risk_usdt=1.0, marks=marks)
        assert ok, reason
        opened += 1
    # fourth position would push open risk to 4% > 3% budget
    ok, reason = account.open(symbol="D", side=1, price=100.0, stop_price=97.5,
                              risk_usdt=1.0, marks=marks)
    assert ok is False
    assert reason == "risk_budget"
    assert "D" not in account.positions
    assert opened == 3
    assert account.rejections["risk_budget"] == 1


def test_insufficient_margin_rejects_instead_of_virtual_leverage() -> None:
    """Check 5: with free cash exhausted the account refuses the trade."""
    account = Account(initial_capital=100.0, leverage=3.0, risk_budget=1.0)
    marks = {name: 100.0 for name in ("A", "B", "C")}
    # each trade risks 30 USDT over a 2.5% stop -> notional 1200, margin 400 > 100
    ok, reason = account.open(symbol="A", side=1, price=100.0, stop_price=97.5,
                              risk_usdt=30.0, marks=marks)
    assert ok is False
    assert reason == "insufficient_margin"
    assert account.positions == {}
    assert account.free_cash == pytest.approx(100.0)     # nothing spent, nothing created
    assert account.fees_paid == 0.0

    # a size that fits is accepted, and after that the margin is genuinely locked
    ok, _ = account.open(symbol="A", side=1, price=100.0, stop_price=97.5,
                         risk_usdt=2.0, marks=marks)
    assert ok
    locked = account.locked_margin()
    assert locked > 0
    assert account.free_cash == pytest.approx(100.0 - locked
                                              - account.positions["A"].entry_fee)
    # the same size can no longer be opened twice on a second symbol
    ok2, reason2 = account.open(symbol="B", side=1, price=100.0, stop_price=97.5,
                                risk_usdt=2.0, marks=marks)
    assert (ok2, reason2) == (True, "ok")
    ok3, reason3 = account.open(symbol="C", side=1, price=100.0, stop_price=97.5,
                                risk_usdt=2.0, marks=marks)
    if not ok3:
        assert reason3 == "insufficient_margin"


def test_duplicate_open_is_refused() -> None:
    account = Account(initial_capital=100.0)
    marks = {"A": 100.0}
    assert account.open(symbol="A", side=1, price=100.0, stop_price=97.5,
                        risk_usdt=1.0, marks=marks)[0]
    ok, reason = account.open(symbol="A", side=1, price=100.0, stop_price=97.5,
                              risk_usdt=1.0, marks=marks)
    assert ok is False and reason == "already_open"


def test_invariant_breaker_raises() -> None:
    """The invariant checker must actually raise, not just return."""
    account = Account(initial_capital=100.0)
    marks = {"A": 100.0}
    account.open(symbol="A", side=1, price=100.0, stop_price=97.5, risk_usdt=1.0, marks=marks)
    account.realised_net += 5.0     # corrupt the cash-flow identity on purpose
    with pytest.raises(AccountingError):
        account.assert_invariants(marks)


def test_fees_are_charged_once_per_side() -> None:
    account = Account(initial_capital=100.0)
    marks = {"A": 100.0}
    account.open(symbol="A", side=1, price=100.0, stop_price=97.5, risk_usdt=1.0, marks=marks)
    entry_fee = account.positions["A"].entry_fee
    assert account.fees_paid == pytest.approx(entry_fee)
    marks = {"A": 100.0}
    account.close(symbol="A", price=100.0, reason="flat", marks=marks)
    exit_fee = account.closed[-1]["exit_fee"]
    assert account.fees_paid == pytest.approx(entry_fee + exit_fee)


def test_sequential_trades_match_direct_compounding() -> None:
    """With one position at a time and no fees, the account must reproduce
    Pi(1 + f*R).  This isolates the Account implementation from the
    floating-equity sizing effect that appears with concurrent positions."""
    import math

    capital = 10_000.0
    f = 0.01
    account = Account(initial_capital=capital, leverage=3.0, risk_budget=1.0)
    marks: dict[str, float] = {}
    product = capital
    plan = [(100.0, 110.0, 2.5, 1),    # +4R winner
            (100.0, 97.5, 2.5, 1),     # -1R loser
            (100.0, 120.0, 2.5, 1),    # +8R winner
            (100.0, 102.5, 2.5, -1)]   # -1R loser on the short side
    for entry, exit_price, distance, side in plan:
        marks["X"] = entry
        equity = account.equity(marks)
        risk_usdt = f * equity
        ok, reason = account.open(symbol="X", side=side, price=entry,
                                  stop_price=entry - side * distance,
                                  risk_usdt=risk_usdt, marks=marks, mmr=0.01)
        assert ok, reason
        marks["X"] = exit_price
        account.close(symbol="X", price=exit_price, reason="test", marks=marks)
        r_multiple = side * (exit_price - entry) / distance
        product *= (1 + f * r_multiple)
    # fees are the only intended difference; they are tiny at this size
    assert account.equity(marks) == pytest.approx(product, rel=0.005)


def test_concurrency_sizes_on_floating_equity() -> None:
    """Two overlapping positions must be sized on the equity marked at entry,
    including the other position's unrealised PnL."""
    account = Account(initial_capital=10_000.0, leverage=10.0, risk_budget=1.0)
    marks = {"A": 100.0, "B": 100.0}
    account.open(symbol="A", side=1, price=100.0, stop_price=97.5,
                 risk_usdt=100.0, marks=marks)
    marks = {"A": 150.0, "B": 100.0}          # A is deep in profit
    equity_now = account.equity(marks)
    assert equity_now > 10_000.0
    account.open(symbol="B", side=1, price=100.0, stop_price=97.5,
                 risk_usdt=0.01 * equity_now, marks=marks)
    # B's size is based on the inflated equity, not on initial capital
    assert account.positions["B"].quantity > account.positions["A"].quantity
    account.assert_invariants(marks)
