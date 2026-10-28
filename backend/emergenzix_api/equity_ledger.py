"""Pure, immutable settled-equity ledger for research simulation.

SYSTEM layer, not a strategy or broker integration. It accounts for CLOSED trades
and explicit external transfers. It cannot represent an open position's mark,
margin, available collateral, fills, staged entries, or liquidation. Those must
be separate, audited modules rather than inferred from settled cash.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation


MoneyLike = str | int | float | Decimal


def _money(value: MoneyLike, *, name: str) -> Decimal:
    """Preserve decimal arithmetic and fail closed on non-finite inputs."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal")
    if not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{name} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


def _event_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a non-empty ID (max 128 chars)")
    return value


@dataclass(frozen=True, slots=True)
class SettledEquityLedger:
    """A flat-account snapshot; not a representation of live free cash.

    `equity = initial_cash + realized_gross - accumulated_costs + external_net`.
    Since open positions are not represented, capital cannot be treated as
    available for new orders unless the portfolio controller confirms flat.
    All fees for a trade are recognized in ONE settlement and nowhere else.
    """

    initial_cash: Decimal
    settled_cash: Decimal
    realized_gross: Decimal = Decimal("0")
    accumulated_costs: Decimal = Decimal("0")
    external_net: Decimal = Decimal("0")
    trade_count: int = 0
    settled_trade_ids: frozenset[str] = frozenset()
    transfer_ids: frozenset[str] = frozenset()

    @classmethod
    def start(cls, initial_cash: MoneyLike) -> SettledEquityLedger:
        capital = _money(initial_cash, name="initial_cash")
        if capital <= 0:
            raise ValueError("initial_cash must be positive")
        return cls(initial_cash=capital, settled_cash=capital)

    @property
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.accumulated_costs

    @property
    def is_insolvent(self) -> bool:
        return self.settled_cash <= 0

    def budget(self, fraction: MoneyLike) -> Decimal:
        """A research budget hint, NOT authorization to place an order."""
        rate = _money(fraction, name="fraction")
        if not Decimal("0") <= rate <= Decimal("1"):
            raise ValueError("fraction must be between 0 and 1")
        return max(Decimal("0"), self.settled_cash) * rate

    def settle_trade(
        self,
        *,
        trade_id: str,
        gross_pnl: MoneyLike,
        entry_fee: MoneyLike = 0,
        exit_fee: MoneyLike = 0,
        other_cost: MoneyLike = 0,
    ) -> SettledEquityLedger:
        """Book the completed trade and both fees exactly once.

        Negative settlement balance is kept as insolvency evidence rather than
        silently clamped or fabricated as a profitable balance.
        """
        event = _event_id(trade_id, "trade_id")
        if event in self.settled_trade_ids:
            raise ValueError("trade already settled")
        gross = _money(gross_pnl, name="gross_pnl")
        charges = tuple(
            _money(value, name=name)
            for name, value in (
                ("entry_fee", entry_fee),
                ("exit_fee", exit_fee),
                ("other_cost", other_cost),
            )
        )
        if any(value < 0 for value in charges):
            raise ValueError("trade costs cannot be negative")
        costs = sum(charges, Decimal("0"))
        return replace(
            self,
            settled_cash=self.settled_cash + gross - costs,
            realized_gross=self.realized_gross + gross,
            accumulated_costs=self.accumulated_costs + costs,
            trade_count=self.trade_count + 1,
            settled_trade_ids=self.settled_trade_ids | {event},
        )

    def transfer(
        self, *, transfer_id: str, amount: MoneyLike
    ) -> SettledEquityLedger:
        """Apply an external deposit (+) or withdrawal (-), never as PnL."""
        event = _event_id(transfer_id, "transfer_id")
        if event in self.transfer_ids:
            raise ValueError("transfer already booked")
        delta = _money(amount, name="amount")
        if delta == 0:
            raise ValueError("transfer amount cannot be zero")
        if delta < 0 and self.settled_cash + delta < 0:
            raise ValueError("withdrawal exceeds settled cash")
        return replace(
            self,
            settled_cash=self.settled_cash + delta,
            external_net=self.external_net + delta,
            transfer_ids=self.transfer_ids | {event},
        )

    def audit(self) -> dict[str, str | int | bool]:
        """Safe research report: serializes money as exact decimal strings."""
        expected = (
            self.initial_cash + self.realized_gross
            - self.accumulated_costs + self.external_net
        )
        if self.settled_cash != expected:
            raise ValueError("ledger conservation invariant violated")
        return {
            "initial_cash": str(self.initial_cash),
            "settled_cash": str(self.settled_cash),
            "realized_gross": str(self.realized_gross),
            "accumulated_costs": str(self.accumulated_costs),
            "realized_net": str(self.realized_net),
            "external_net": str(self.external_net),
            "closed_trade_count": self.trade_count,
            "is_insolvent": self.is_insolvent,
            "open_position_mark_to_market_included": False,
            "tranches_supported": False,
            "orders_created": False,
        }
