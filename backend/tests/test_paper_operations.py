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
