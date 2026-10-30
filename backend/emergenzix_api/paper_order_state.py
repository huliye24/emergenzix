"""Deterministic PAPER order state transitions.

The reducer is deliberately independent from HTTP, SQLite, and exchanges.  A
caller must persist every accepted snapshot and event atomically before using
the resulting state for positions or accounting.  LIVE execution is out of
scope.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal


OrderState = Literal[
    "CREATED",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "UNKNOWN",
]
EventType = Literal[
    "SUBMIT",
    "ACKNOWLEDGE",
    "FILL",
    "REQUEST_CANCEL",
    "CANCEL",
    "REJECT",
    "EXPIRE",
    "OUTCOME_UNKNOWN",
    "RECONCILE_OPEN",
]

TERMINAL_STATES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})


class InvalidOrderTransition(ValueError):
    """Raised when accepting an event would make order state ambiguous."""


@dataclass(frozen=True, slots=True)
class PaperOrderEvent:
    event_id: str
    event_type: EventType
    fill_quantity: Decimal = Decimal("0")
    fill_price: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class PaperOrderSnapshot:
    order_id: str
    requested_quantity: Decimal
    state: OrderState = "CREATED"
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    processed_event_ids: tuple[str, ...] = ()

    @property
    def remaining_quantity(self) -> Decimal:
        return self.requested_quantity - self.filled_quantity

    def to_record(self) -> dict[str, str | list[str]]:
        """Return a JSON-safe snapshot suitable for durable persistence."""

        return {
            "order_id": self.order_id,
            "requested_quantity": str(self.requested_quantity),
            "state": self.state,
            "filled_quantity": str(self.filled_quantity),
            "average_fill_price": str(self.average_fill_price),
            "fees": str(self.fees),
            "processed_event_ids": list(self.processed_event_ids),
        }

    @classmethod
    def from_record(cls, record: dict[str, object]) -> "PaperOrderSnapshot":
        return cls(
            order_id=str(record["order_id"]),
            requested_quantity=Decimal(str(record["requested_quantity"])),
            state=str(record["state"]),  # type: ignore[arg-type]
            filled_quantity=Decimal(str(record["filled_quantity"])),
            average_fill_price=Decimal(str(record["average_fill_price"])),
            fees=Decimal(str(record["fees"])),
            processed_event_ids=tuple(str(item) for item in record["processed_event_ids"]),  # type: ignore[union-attr]
        )


def new_paper_order(order_id: str, requested_quantity: Decimal) -> PaperOrderSnapshot:
    if not order_id.strip() or not requested_quantity.is_finite() or requested_quantity <= 0:
        raise ValueError("order_id and a positive finite requested quantity are required")
    return PaperOrderSnapshot(order_id=order_id, requested_quantity=requested_quantity)


def _accept(snapshot: PaperOrderSnapshot, event: PaperOrderEvent, **changes: object) -> PaperOrderSnapshot:
    return replace(
        snapshot,
        processed_event_ids=(*snapshot.processed_event_ids, event.event_id),
        **changes,
    )


def apply_order_event(snapshot: PaperOrderSnapshot, event: PaperOrderEvent) -> PaperOrderSnapshot:
    """Apply one idempotent event or fail closed on an illegal transition."""

    if not event.event_id.strip():
        raise InvalidOrderTransition("event_id is required")
    if event.event_id in snapshot.processed_event_ids:
        return snapshot
    if snapshot.state in TERMINAL_STATES:
        raise InvalidOrderTransition(f"terminal order {snapshot.state} cannot transition")

    state = snapshot.state
    if event.event_type == "SUBMIT" and state == "CREATED":
        return _accept(snapshot, event, state="SUBMITTED")
    if event.event_type == "ACKNOWLEDGE" and state in {"SUBMITTED", "UNKNOWN"}:
        next_state = "PARTIALLY_FILLED" if snapshot.filled_quantity else "ACKNOWLEDGED"
        return _accept(snapshot, event, state=next_state)
    if event.event_type == "REQUEST_CANCEL" and state in {
        "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN"
    }:
        return _accept(snapshot, event, state="CANCEL_PENDING")
    if event.event_type == "OUTCOME_UNKNOWN" and state != "CREATED":
        return _accept(snapshot, event, state="UNKNOWN")
    if event.event_type == "RECONCILE_OPEN" and state == "UNKNOWN":
        next_state = "PARTIALLY_FILLED" if snapshot.filled_quantity else "ACKNOWLEDGED"
        return _accept(snapshot, event, state=next_state)
    if event.event_type == "CANCEL" and state in {
        "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING", "UNKNOWN"
    }:
        return _accept(snapshot, event, state="CANCELED")
    if event.event_type == "REJECT" and state in {"SUBMITTED", "ACKNOWLEDGED", "UNKNOWN"}:
        return _accept(snapshot, event, state="REJECTED")
    if event.event_type == "EXPIRE" and state in {
        "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING", "UNKNOWN"
    }:
        return _accept(snapshot, event, state="EXPIRED")
    if event.event_type == "FILL" and state in {
        "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING", "UNKNOWN"
    }:
        if (
            not event.fill_quantity.is_finite()
            or not event.fill_price.is_finite()
            or not event.fee.is_finite()
            or event.fill_quantity <= 0
            or event.fill_price <= 0
            or event.fee < 0
        ):
            raise InvalidOrderTransition("fill quantity/price must be positive and fee non-negative")
        new_quantity = snapshot.filled_quantity + event.fill_quantity
        if new_quantity > snapshot.requested_quantity:
            raise InvalidOrderTransition("cumulative fill exceeds requested quantity")
        old_value = snapshot.filled_quantity * snapshot.average_fill_price
        average = (old_value + event.fill_quantity * event.fill_price) / new_quantity
        next_state = "FILLED" if new_quantity == snapshot.requested_quantity else "PARTIALLY_FILLED"
        return _accept(
            snapshot,
            event,
            state=next_state,
            filled_quantity=new_quantity,
            average_fill_price=average,
            fees=snapshot.fees + event.fee,
        )

    raise InvalidOrderTransition(f"illegal transition: {state} + {event.event_type}")
