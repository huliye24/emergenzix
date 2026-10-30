from decimal import Decimal
import sqlite3

import pytest

from emergenzix_api.database import Database
from emergenzix_api.paper_order_state import PaperOrderEvent
from emergenzix_api.paper_order_store import PaperOrderRecoveryError, PaperOrderStore


def fill(event_id: str, quantity: str) -> PaperOrderEvent:
    return PaperOrderEvent(
        event_id=event_id,
        event_type="FILL",
        fill_quantity=Decimal(quantity),
        fill_price=Decimal("100"),
        fee=Decimal("0.1"),
    )


def test_event_and_snapshot_are_persisted_atomically(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    store = PaperOrderStore(database)
    store.create("order-1", Decimal("10"))
    store.apply("order-1", PaperOrderEvent("submit", "SUBMIT"))
    partial = store.apply("order-1", fill("fill-1", "4"))

    with database.connection() as connection:
        events = connection.execute(
            "SELECT count(*) AS count FROM paper_order_events WHERE order_id = ?",
            ("order-1",),
        ).fetchone()["count"]
        state = connection.execute(
            "SELECT state FROM paper_order_snapshots WHERE order_id = ?",
            ("order-1",),
        ).fetchone()["state"]
    assert events == 2
    assert state == "PARTIALLY_FILLED"
    assert partial.filled_quantity == Decimal("4")


def test_duplicate_event_is_idempotent_and_conflicting_payload_is_rejected(tmp_path):
    store = PaperOrderStore(Database(str(tmp_path / "paper.db")))
    store.create("order-1", Decimal("10"))
    store.apply("order-1", PaperOrderEvent("submit", "SUBMIT"))
    once = store.apply("order-1", fill("fill-1", "4"))
    assert store.apply("order-1", fill("fill-1", "4")) == once
    with pytest.raises(ValueError, match="different event data"):
        store.apply("order-1", fill("fill-1", "5"))


def test_database_reopen_recovers_snapshot_and_processed_events(tmp_path):
    path = tmp_path / "paper.db"
    first = PaperOrderStore(Database(str(path)))
    first.create("order-1", Decimal("10"))
    first.apply("order-1", PaperOrderEvent("submit", "SUBMIT"))
    expected = first.apply("order-1", fill("fill-1", "3.5"))

    recovered = PaperOrderStore(Database(str(path))).get("order-1")
    assert recovered == expected
    assert recovered.processed_event_ids == ("submit", "fill-1")


def test_order_events_cannot_be_updated_or_deleted(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    store = PaperOrderStore(database)
    store.create("order-1", Decimal("1"))
    store.apply("order-1", PaperOrderEvent("submit", "SUBMIT"))

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connection() as connection:
            connection.execute(
                "UPDATE paper_order_events SET event_type = 'REJECT' WHERE event_id = 'submit'"
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connection() as connection:
            connection.execute("DELETE FROM paper_order_events WHERE event_id = 'submit'")


def test_restart_replays_events_and_verifies_saved_snapshot(tmp_path):
    path = tmp_path / "paper.db"
    store = PaperOrderStore(Database(str(path)))
    store.create("order-1", Decimal("10"))
    store.apply("order-1", PaperOrderEvent("submit", "SUBMIT"))
    expected = store.apply("order-1", fill("fill-1", "4"))
    restarted = PaperOrderStore(Database(str(path)))
    assert restarted.replay_and_verify("order-1") == expected


def test_restart_fails_closed_when_snapshot_diverges_from_event_log(tmp_path):
    path = tmp_path / "paper.db"
    database = Database(str(path))
    store = PaperOrderStore(database)
    store.create("order-1", Decimal("10"))
    store.apply("order-1", PaperOrderEvent("submit", "SUBMIT"))
    with database.connection() as connection:
        connection.execute(
            "UPDATE paper_order_snapshots SET snapshot_json = replace(" 
            "snapshot_json, '\"state\":\"SUBMITTED\"', '\"state\":\"UNKNOWN\"') "
            "WHERE order_id = 'order-1'"
        )
    with pytest.raises(PaperOrderRecoveryError, match="diverges"):
        PaperOrderStore(Database(str(path))).replay_and_verify("order-1")
