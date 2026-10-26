"""S-005.A — audited account ledger with margin, liquidation and a unified budget.

Purpose: the research so far compounded per-trade R as if capital were always
available.  Real accounts have free cash, locked margin, maintenance margin and
liquidation.  This module models exactly that, and refuses to create virtual
purchasing power.

Model:
    equity      = free_cash + sum(margin locked) + sum(unrealised net PnL)
    available   = free_cash                      (what a new position may use)
    open cost   = entry fee + margin, paid from free_cash
    close       = margin released + gross PnL, exit fee charged
    liquidation = equity <= maintenance margin, forced flat at a penalty

Invariants asserted on every mark:
    I1  free_cash >= 0
    I2  equity == free_cash + locked margin + unrealised
    I3  sum(margin) <= initial capital + realised net (no synthetic leverage)
    I4  realised_net == sum of closed-trade net PnL
    I5  equity >= 0

Research only: no order routing, no exchange client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

FEE_RATE = 0.0005
LIQUIDATION_PENALTY = 0.01          # 1% of notional, charged on forced liquidation
DEFAULT_MMR = 0.010                 # maintenance margin rate (1% of notional)


class AccountingError(RuntimeError):
    """Raised when a ledger invariant is violated; never silently ignored."""


@dataclass
class Position:
    symbol: str
    side: int                       # +1 long, -1 short
    quantity: float
    entry_price: float
    margin: float
    entry_fee: float
    mmr: float
    stop_price: float | None = None

    def notional(self, price: float) -> float:
        return abs(self.quantity) * price

    def unrealised(self, price: float) -> float:
        """Gross mark-to-market minus the entry fee already paid from cash."""
        gross = (price - self.entry_price) * self.quantity * self.side
        return gross - self.entry_fee

    def maintenance(self, price: float) -> float:
        return self.notional(price) * self.mmr


@dataclass
class Account:
    initial_capital: float
    leverage: float = 3.0
    risk_budget: float = 0.075      # max aggregate open risk as a fraction of equity
    free_cash: float = field(init=False)
    positions: Dict[str, Position] = field(default_factory=dict)
    realised_net: float = 0.0
    fees_paid: float = 0.0
    liquidations: int = 0
    rejections: Dict[str, int] = field(default_factory=lambda: {
        "insufficient_margin": 0, "risk_budget": 0, "liquidated": 0})
    closed: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.free_cash = float(self.initial_capital)

    # ---------- views ----------
    def locked_margin(self) -> float:
        return sum(p.margin for p in self.positions.values())

    def unrealised(self, marks: Dict[str, float]) -> float:
        return sum(p.unrealised(marks[p.symbol]) for p in self.positions.values()
                   if p.symbol in marks)

    def equity(self, marks: Dict[str, float]) -> float:
        return self.free_cash + self.locked_margin() + self.unrealised(marks)

    def open_risk(self, marks: Dict[str, float]) -> float:
        """Risk still exposed: distance from mark to stop, in account currency."""
        total = 0.0
        for p in self.positions.values():
            if p.stop_price is None or p.symbol not in marks:
                continue
            adverse = (marks[p.symbol] - p.stop_price) * p.side
            total += max(0.0, adverse) * p.quantity
        return total

    def available(self) -> float:
        return max(0.0, self.free_cash)

    # ---------- invariants ----------
    def assert_invariants(self, marks: Dict[str, float]) -> None:
        equity = self.equity(marks)
        if self.free_cash < -1e-9:
            raise AccountingError(f"I1 free_cash negative: {self.free_cash}")
        # I2 is NOT tautological: entry fees have already left free_cash but only
        # reach realised_net on close, so they must bridge the two sides.
        open_fees = sum(p.entry_fee for p in self.positions.values())
        lhs = self.free_cash + self.locked_margin() + open_fees
        rhs = self.initial_capital + self.realised_net
        if abs(lhs - rhs) > 1e-6:
            raise AccountingError(f"I2 cash flow mismatch: {lhs} vs {rhs}")
        cap = self.initial_capital + self.realised_net
        if self.locked_margin() > cap + 1e-6:
            raise AccountingError(f"I3 margin {self.locked_margin()} exceeds capital base {cap}")
        closed_total = sum(t["net_pnl"] for t in self.closed)
        if abs(self.realised_net - closed_total) > 1e-6:
            raise AccountingError(f"I4 realised {self.realised_net} != closed sum {closed_total}")
        if equity < -1e-9:
            raise AccountingError(f"I5 negative equity: {equity}")

    # ---------- actions ----------
    def can_open(self, *, notional: float, risk_usdt: float,
                 marks: Dict[str, float]) -> tuple[bool, str]:
        if self.positions and self.equity(marks) <= 0:
            return False, "liquidated"
        margin = notional / self.leverage
        if margin > self.free_cash + 1e-9:
            return False, "insufficient_margin"
        budget = self.risk_budget * max(self.equity(marks), 0.0)
        if self.open_risk(marks) + risk_usdt > budget + 1e-9:
            return False, "risk_budget"
        return True, "ok"

    def open(self, *, symbol: str, side: int, price: float, stop_price: float,
             risk_usdt: float, marks: Dict[str, float],
             mmr: float = DEFAULT_MMR) -> tuple[bool, str]:
        if symbol in self.positions:
            return False, "already_open"
        quantity = risk_usdt / abs(price - stop_price)
        notional = quantity * price
        ok, reason = self.can_open(notional=notional, risk_usdt=risk_usdt, marks=marks)
        if not ok:
            self.rejections[reason] = self.rejections.get(reason, 0) + 1
            return False, reason
        fee = notional * FEE_RATE
        margin = notional / self.leverage
        self.free_cash -= (fee + margin)
        self.fees_paid += fee
        self.positions[symbol] = Position(
            symbol=symbol, side=side, quantity=quantity, entry_price=price,
            margin=margin, entry_fee=fee, mmr=mmr, stop_price=stop_price)
        self.assert_invariants(marks)
        return True, "ok"

    def close(self, *, symbol: str, price: float, reason: str,
              marks: Dict[str, float]) -> float:
        position = self.positions.pop(symbol)
        gross = (price - position.entry_price) * position.quantity * position.side
        exit_fee = position.notional(price) * FEE_RATE
        net = gross - position.entry_fee - exit_fee
        self.free_cash += position.margin + gross - exit_fee
        self.realised_net += net
        self.fees_paid += exit_fee
        self.closed.append({"symbol": symbol, "reason": reason, "net_pnl": net,
                            "gross": gross, "entry_fee": position.entry_fee,
                            "exit_fee": exit_fee, "margin": position.margin})
        self.assert_invariants(marks)
        return net

    def mark(self, marks: Dict[str, float], mmr: float = DEFAULT_MMR) -> bool:
        """Mark to market and liquidate if equity is below maintenance margin.

        The breach itself is the liquidation trigger, so it must be evaluated
        BEFORE asserting that equity is non-negative.
        """
        if self.positions:
            equity = self.equity(marks)
            maintenance = sum(p.maintenance(marks.get(p.symbol, p.entry_price))
                              for p in self.positions.values())
            if equity <= maintenance or equity <= 0:
                self._liquidate(marks, mmr)
                return True
        self.assert_invariants(marks)
        return False

    def liquidation_price(self, position: Position, marks: Dict[str, float],
                          ) -> float | None:
        """Price at which equity equals this position's maintenance margin.

        Bisection on (equity - maintenance).  Liquidating at this price (rather
        than at the bar mark) is what keeps a liquidation loss bounded by the
        posted margin, so free cash never goes negative.
        """
        def excess(price: float) -> float:
            probe = dict(marks)
            probe[position.symbol] = price
            return self.equity(probe) - position.maintenance(price)

        low = position.entry_price * 0.01
        high = position.entry_price * 100.0
        f_low, f_high = excess(low), excess(high)
        if f_low * f_high > 0:
            return None
        for _ in range(80):
            middle = (low + high) / 2
            if excess(low) * excess(middle) <= 0:
                high = middle
            else:
                low = middle
        return (low + high) / 2

    def _liquidate(self, marks: Dict[str, float], mmr: float) -> None:
        for symbol in list(self.positions):
            position = self.positions.pop(symbol)
            price = self.liquidation_price(position, marks)
            if price is None:
                price = marks.get(symbol, position.entry_price)
            gross = (price - position.entry_price) * position.quantity * position.side
            exit_fee = position.notional(price) * FEE_RATE
            penalty = position.notional(price) * LIQUIDATION_PENALTY
            cash_delta = position.margin + gross - exit_fee - penalty
            capped = cash_delta < 0
            if capped:
                # Losses cannot exceed the posted margin; keep the ledger identity
                # exact by treating the shortfall as part of the same trade.
                gross = exit_fee + penalty - position.margin
                cash_delta = 0.0
            net = gross - position.entry_fee - exit_fee - penalty
            self.free_cash += cash_delta
            self.realised_net += net
            self.fees_paid += exit_fee + penalty
            self.closed.append({"symbol": symbol, "reason": "liquidation",
                                "net_pnl": net, "gross": gross,
                                "entry_fee": position.entry_fee, "exit_fee": exit_fee,
                                "penalty": penalty, "margin": position.margin,
                                "liquidation_price": price, "loss_capped": capped})
            self.liquidations += 1
        self.assert_invariants(marks)
