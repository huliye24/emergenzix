"""Deterministic long-only PAPER position projection from fills."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class PositionFill:
    fill_event_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class PaperPosition:
    symbol: str
    quantity: Decimal = Decimal("0")
    average_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    processed_fill_ids: tuple[str, ...] = ()


def empty_position(symbol: str) -> PaperPosition:
    if not symbol.strip():
        raise ValueError("symbol is required")
    return PaperPosition(symbol=symbol)


def project_fill(position: PaperPosition, fill: PositionFill) -> PaperPosition:
    if fill.fill_event_id in position.processed_fill_ids:
        return position
    if not fill.fill_event_id.strip() or fill.symbol != position.symbol:
        raise ValueError("fill identifier and matching symbol are required")
    if (
        not fill.quantity.is_finite()
        or not fill.price.is_finite()
        or fill.quantity <= 0
        or fill.price <= 0
    ):
        raise ValueError("fill quantity and price must be positive and finite")

    processed = (*position.processed_fill_ids, fill.fill_event_id)
    if fill.side == "BUY":
        new_quantity = position.quantity + fill.quantity
        new_cost = (
            position.quantity * position.average_cost + fill.quantity * fill.price
        ) / new_quantity
        return replace(
            position,
            quantity=new_quantity,
            average_cost=new_cost,
            processed_fill_ids=processed,
        )
    if fill.side == "SELL":
        if fill.quantity > position.quantity:
            raise ValueError("sell fill exceeds projected position")
        realized = (fill.price - position.average_cost) * fill.quantity
        remaining = position.quantity - fill.quantity
        return replace(
            position,
            quantity=remaining,
            average_cost=position.average_cost if remaining else Decimal("0"),
            realized_pnl=position.realized_pnl + realized,
            processed_fill_ids=processed,
        )
    raise ValueError(f"unsupported side: {fill.side}")
