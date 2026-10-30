from decimal import Decimal

import pytest

from emergenzix_api.paper_order_state import (
    InvalidOrderTransition,
    PaperOrderEvent,
    PaperOrderSnapshot,
    apply_order_event,
    new_paper_order,
)


def event(event_id: str, event_type: str, **values: str) -> PaperOrderEvent:
    return PaperOrderEvent(
        event_id=event_id,
        event_type=event_type,  # type: ignore[arg-type]
        fill_quantity=Decimal(values.get("quantity", "0")),
        fill_price=Decimal(values.get("price", "0")),
        fee=Decimal(values.get("fee", "0")),
    )


def submitted(quantity: str = "10") -> PaperOrderSnapshot:
    return apply_order_event(new_paper_order("paper-1", Decimal(quantity)), event("e1", "SUBMIT"))


def test_duplicate_event_is_idempotent():
    order = submitted()
    fill = event("fill-1", "FILL", quantity="4", price="100", fee="0.4")
    once = apply_order_event(order, fill)
    twice = apply_order_event(once, fill)
    assert twice == once
    assert twice.filled_quantity == Decimal("4")


def test_partial_fills_accumulate_exact_quantity_price_and_fees():
    first = apply_order_event(
        submitted(), event("fill-1", "FILL", quantity="4", price="100", fee="0.4")
    )
    completed = apply_order_event(
        first, event("fill-2", "FILL", quantity="6", price="110", fee="0.66")
    )
    assert first.state == "PARTIALLY_FILLED"
    assert first.remaining_quantity == Decimal("6")
    assert completed.state == "FILLED"
    assert completed.average_fill_price == Decimal("106")
    assert completed.fees == Decimal("1.06")


def test_fill_after_cancel_request_is_accepted_because_cancel_can_race():
    pending = apply_order_event(submitted(), event("cancel-request", "REQUEST_CANCEL"))
    filled = apply_order_event(
        pending, event("fill-race", "FILL", quantity="10", price="100", fee="1")
    )
    assert filled.state == "FILLED"


def test_unknown_outcome_blocks_until_reconciled():
    unknown = apply_order_event(submitted(), event("timeout", "OUTCOME_UNKNOWN"))
    reconciled = apply_order_event(unknown, event("query-result", "RECONCILE_OPEN"))
    assert unknown.state == "UNKNOWN"
    assert reconciled.state == "ACKNOWLEDGED"


def test_snapshot_round_trip_supports_restart_recovery():
    partial = apply_order_event(
        submitted(), event("fill-1", "FILL", quantity="3.5", price="101.25", fee="0.2")
    )
    restored = PaperOrderSnapshot.from_record(partial.to_record())
    assert restored == partial
    assert apply_order_event(restored, event("fill-1", "FILL", quantity="3.5", price="101.25")) == restored


def test_overfill_illegal_transition_and_terminal_mutation_fail_closed():
    with pytest.raises(InvalidOrderTransition, match="exceeds"):
        apply_order_event(
            submitted(), event("overfill", "FILL", quantity="11", price="100")
        )
    with pytest.raises(InvalidOrderTransition, match="illegal transition"):
        apply_order_event(new_paper_order("paper-2", Decimal("1")), event("ack", "ACKNOWLEDGE"))
    filled = apply_order_event(
        submitted(), event("full", "FILL", quantity="10", price="100")
    )
    with pytest.raises(InvalidOrderTransition, match="terminal"):
        apply_order_event(filled, event("late-cancel", "CANCEL"))
