from decimal import Decimal

import pytest

from emergenzix_api.database import Database
from emergenzix_api.paper_kill_switch import get_paper_kill_switch, set_paper_kill_switch
from emergenzix_api.paper_operations import (
    PaperOperationRejected,
    PaperOperations,
    PaperOrderIntent,
)
from emergenzix_api.paper_risk_gate import PaperRiskSnapshot


SAFE = PaperRiskSnapshot(True, True, True, ())
BLOCKED = PaperRiskSnapshot(False, True, True, ("market_data_stale",))


def intent(order_id="order-1", key="key-1", quantity="2"):
    return PaperOrderIntent(order_id, key, "BTCUSDT", "BUY", Decimal(quantity))


def activate(database):
    current = get_paper_kill_switch(database)
    set_paper_kill_switch(
        database, paused=False, reason="paper v2 test", expected_revision=current.revision
    )


def test_default_kill_switch_blocks_new_orders(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    with pytest.raises(PaperOperationRejected, match="kill switch"):
        PaperOperations(database).submit(intent(), SAFE)


def test_stale_risk_blocks_without_leaving_partial_order(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)
    with pytest.raises(PaperOperationRejected, match="risk gate"):
        PaperOperations(database).submit(intent(), BLOCKED)
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paper_v2_order_intents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM paper_order_snapshots").fetchone()[0] == 0


def test_submit_is_atomic_and_idempotent(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)
    operations = PaperOperations(database)
    first = operations.submit(intent(), SAFE)
    repeated = operations.submit(intent(), BLOCKED)
    assert repeated == first
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paper_v2_order_intents").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM paper_order_events").fetchone()[0] == 1


def test_fault_after_snapshot_rolls_back_every_write(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)

    def fail(point):
        if point == "after_snapshot":
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        PaperOperations(database, fault_injector=fail).submit(intent(), SAFE)
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paper_v2_order_intents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM paper_order_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM paper_order_events").fetchone()[0] == 0


def test_idempotency_key_conflict_is_rejected(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)
    operations = PaperOperations(database)
    operations.submit(intent(), SAFE)
    with pytest.raises(PaperOperationRejected, match="conflicts"):
        operations.submit(intent(order_id="order-2", quantity="3"), SAFE)


def test_fill_atomically_updates_order_ledger_and_position(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)
    operations = PaperOperations(database)
    operations.submit(intent(), SAFE)
    order, position = operations.record_fill(
        "order-1", fill_event_id="fill-1", quantity=Decimal("2"),
        price=Decimal("100"), fee=Decimal("0.2"),
    )
    assert order.state == "FILLED"
    assert position.quantity == Decimal("2")
    with database.connection() as connection:
        entries = connection.execute("SELECT debit, credit FROM paper_ledger_entries").fetchall()
        debits = sum((Decimal(row["debit"]) for row in entries), Decimal("0"))
        credits = sum((Decimal(row["credit"]) for row in entries), Decimal("0"))
    assert debits == credits == Decimal("200.2")


def test_fill_processing_continues_after_new_orders_are_paused(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)
    operations = PaperOperations(database)
    operations.submit(intent(), SAFE)
    current = get_paper_kill_switch(database)
    set_paper_kill_switch(
        database, paused=True, reason="operator stop", expected_revision=current.revision
    )
    order, _ = operations.record_fill(
        "order-1", fill_event_id="fill-1", quantity=Decimal("2"),
        price=Decimal("100"), fee=Decimal("0.2"),
    )
    assert order.state == "FILLED"


def test_fault_after_ledger_rolls_back_event_ledger_and_position(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    activate(database)
    PaperOperations(database).submit(intent(), SAFE)

    def fail(point):
        if point == "after_ledger":
            raise RuntimeError("injected fill crash")

    with pytest.raises(RuntimeError, match="injected fill crash"):
        PaperOperations(database, fault_injector=fail).record_fill(
            "order-1", fill_event_id="fill-1", quantity=Decimal("2"),
            price=Decimal("100"), fee=Decimal("0.2"),
        )
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paper_ledger_transactions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM paper_v2_positions").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM paper_order_snapshots").fetchone()[0] == "SUBMITTED"
