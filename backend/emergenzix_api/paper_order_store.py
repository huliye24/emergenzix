"""Transactional SQLite storage for PAPER order events and snapshots."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from .database import Database
from .paper_order_state import (
    PaperOrderEvent,
    PaperOrderSnapshot,
    apply_order_event,
    new_paper_order,
)


class PaperOrderNotFound(LookupError):
    pass


class PaperOrderStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, order_id: str, requested_quantity: Decimal) -> PaperOrderSnapshot:
        snapshot = new_paper_order(order_id, requested_quantity)
        encoded = self._encode_snapshot(snapshot)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.database.connection() as connection:
                connection.execute(
                    "INSERT INTO paper_order_snapshots "
                    "(order_id, snapshot_json, state, updated_at) VALUES (?, ?, ?, ?)",
                    (snapshot.order_id, encoded, snapshot.state, now),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"paper order already exists: {order_id}") from error
        return snapshot

    def get(self, order_id: str) -> PaperOrderSnapshot:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM paper_order_snapshots WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        if row is None:
            raise PaperOrderNotFound(order_id)
        return PaperOrderSnapshot.from_record(json.loads(row["snapshot_json"]))

    def apply(self, order_id: str, event: PaperOrderEvent) -> PaperOrderSnapshot:
        """Persist an event and its resulting snapshot in one transaction."""

        now = datetime.now(timezone.utc).isoformat()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT snapshot_json FROM paper_order_snapshots WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                raise PaperOrderNotFound(order_id)
            current = PaperOrderSnapshot.from_record(json.loads(row["snapshot_json"]))

            existing = connection.execute(
                "SELECT event_json FROM paper_order_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            event_json = self._encode_event(event)
            if existing is not None:
                if existing["event_json"] != event_json:
                    raise ValueError("event_id was already used for different event data")
                return current

            updated = apply_order_event(current, event)
            connection.execute(
                "INSERT INTO paper_order_events "
                "(event_id, order_id, event_type, event_json, resulting_state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    order_id,
                    event.event_type,
                    event_json,
                    updated.state,
                    now,
                ),
            )
            connection.execute(
                "UPDATE paper_order_snapshots SET snapshot_json = ?, state = ?, updated_at = ? "
                "WHERE order_id = ?",
                (self._encode_snapshot(updated), updated.state, now, order_id),
            )
        return updated

    @staticmethod
    def _encode_snapshot(snapshot: PaperOrderSnapshot) -> str:
        return json.dumps(snapshot.to_record(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _encode_event(event: PaperOrderEvent) -> str:
        return json.dumps(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "fill_quantity": str(event.fill_quantity),
                "fill_price": str(event.fill_price),
                "fee": str(event.fee),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
