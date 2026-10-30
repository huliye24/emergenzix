"""Balanced, deterministic accounting entries for PAPER fills."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


Side = Literal["BUY", "SELL"]


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class FillJournal:
    transaction_id: str
    order_id: str
    fill_event_id: str
    entries: tuple[LedgerEntry, ...]
    realized_pnl: Decimal

    @property
    def total_debits(self) -> Decimal:
        return sum((entry.debit for entry in self.entries), Decimal("0"))

    @property
    def total_credits(self) -> Decimal:
        return sum((entry.credit for entry in self.entries), Decimal("0"))

    @property
    def balanced(self) -> bool:
        return self.total_debits == self.total_credits


def journal_fill(
    *,
    transaction_id: str,
    order_id: str,
    fill_event_id: str,
    side: Side,
    quantity: Decimal,
    price: Decimal,
    fee: Decimal,
    inventory_unit_cost: Decimal | None = None,
) -> FillJournal:
    """Create balanced entries without mutating an account or position."""

    if not transaction_id.strip() or not order_id.strip() or not fill_event_id.strip():
        raise ValueError("transaction, order, and fill event identifiers are required")
    numeric = (quantity, price, fee)
    if any(not value.is_finite() for value in numeric):
        raise ValueError("quantity, price, and fee must be finite")
    if quantity <= 0 or price <= 0 or fee < 0:
        raise ValueError("quantity and price must be positive and fee non-negative")
    notional = quantity * price

    if side == "BUY":
        if inventory_unit_cost is not None:
            raise ValueError("BUY journal does not accept inventory_unit_cost")
        entries = (
            LedgerEntry("ASSET_INVENTORY", debit=notional),
            LedgerEntry("TRADING_FEES", debit=fee),
            LedgerEntry("CASH", credit=notional + fee),
        )
        realized_pnl = Decimal("0")
    elif side == "SELL":
        if inventory_unit_cost is None or not inventory_unit_cost.is_finite():
            raise ValueError("SELL journal requires a finite inventory_unit_cost")
        if inventory_unit_cost <= 0:
            raise ValueError("inventory_unit_cost must be positive")
        cost = quantity * inventory_unit_cost
        realized_pnl = notional - cost
        mutable = [
            LedgerEntry("CASH", debit=notional - fee),
            LedgerEntry("TRADING_FEES", debit=fee),
            LedgerEntry("ASSET_INVENTORY", credit=cost),
        ]
        if realized_pnl > 0:
            mutable.append(LedgerEntry("REALIZED_PNL", credit=realized_pnl))
        elif realized_pnl < 0:
            mutable.append(LedgerEntry("REALIZED_PNL", debit=-realized_pnl))
        entries = tuple(mutable)
    else:
        raise ValueError(f"unsupported side: {side}")

    journal = FillJournal(
        transaction_id=transaction_id,
        order_id=order_id,
        fill_event_id=fill_event_id,
        entries=entries,
        realized_pnl=realized_pnl,
    )
    if not journal.balanced:
        raise AssertionError("generated journal is not balanced")
    return journal
