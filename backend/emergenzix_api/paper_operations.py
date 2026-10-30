"""Transactional orchestration boundary for PAPER v2 operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Literal

from .database import Database
from .paper_accounting import journal_fill
from .paper_order_state import PaperOrderEvent, PaperOrderSnapshot, apply_order_event, new_paper_order
from .paper_position_projection import PaperPosition, PositionFill, empty_position, project_fill
from .paper_risk_gate import PaperRiskSnapshot


class PaperOperationRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    order_id: str
    idempotency_key: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal


class PaperOperations:
    def __init__(
        self,
        database: Database,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.database = database
        self.fault_injector = fault_injector or (lambda _point: None)

    def submit(self, intent: PaperOrderIntent, risk: PaperRiskSnapshot) -> PaperOrderSnapshot:
        request_json = self._request_json(intent)
        request_sha256 = hashlib.sha256(request_json.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT request_sha256, order_id FROM paper_v2_order_intents "
                "WHERE idempotency_key = ?",
                (intent.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise PaperOperationRejected("idempotency key conflicts with another intent")
                row = connection.execute(
                    "SELECT snapshot_json FROM paper_order_snapshots WHERE order_id = ?",
                    (existing["order_id"],),
                ).fetchone()
                return PaperOrderSnapshot.from_record(json.loads(row["snapshot_json"]))

            if not risk.allow_new_orders:
                raise PaperOperationRejected("risk gate blocked new PAPER order")
            control = connection.execute(
                "SELECT new_orders_paused FROM paper_operational_control WHERE id = 1"
            ).fetchone()
            if control is None or bool(control["new_orders_paused"]):
                raise PaperOperationRejected("PAPER kill switch blocks new orders")

            created = new_paper_order(intent.order_id, intent.quantity)
            submitted = apply_order_event(
                created, PaperOrderEvent(f"{intent.order_id}:submitted", "SUBMIT")
            )
            connection.execute(
                "INSERT INTO paper_order_snapshots "
                "(order_id, snapshot_json, state, updated_at) VALUES (?, ?, ?, ?)",
                (intent.order_id, self._snapshot_json(submitted), submitted.state, now),
            )
            self.fault_injector("after_snapshot")
            connection.execute(
                "INSERT INTO paper_v2_order_intents "
                "(order_id, idempotency_key, request_sha256, symbol, side, "
                "requested_quantity, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    intent.order_id, intent.idempotency_key, request_sha256,
                    intent.symbol, intent.side, str(intent.quantity), now,
                ),
            )
            event = PaperOrderEvent(f"{intent.order_id}:submitted", "SUBMIT")
            connection.execute(
                "INSERT INTO paper_order_events "
                "(event_id, order_id, event_type, event_json, resulting_state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id, intent.order_id, event.event_type,
                    self._event_json(event), submitted.state, now,
                ),
            )
            self.fault_injector("after_event")
        return submitted

    def record_fill(
        self,
        order_id: str,
        *,
        fill_event_id: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
    ) -> tuple[PaperOrderSnapshot, PaperPosition]:
        """Atomically persist fill state, balanced entries, and position projection."""

        now = datetime.now(timezone.utc).isoformat()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM paper_v2_order_intents WHERE order_id = ?", (order_id,)
            ).fetchone()
            if intent is None:
                raise PaperOperationRejected("PAPER v2 order does not exist")
            snapshot_row = connection.execute(
                "SELECT snapshot_json FROM paper_order_snapshots WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            snapshot = PaperOrderSnapshot.from_record(json.loads(snapshot_row["snapshot_json"]))
            event = PaperOrderEvent(fill_event_id, "FILL", quantity, price, fee)
            event_json = self._event_json(event)
            existing = connection.execute(
                "SELECT event_json FROM paper_order_events WHERE event_id = ?",
                (fill_event_id,),
            ).fetchone()
            if existing is not None:
                if existing["event_json"] != event_json:
                    raise PaperOperationRejected("fill event id conflicts with different data")
                position = self._load_position(connection, intent["symbol"])
                return snapshot, position

            position = self._load_position(connection, intent["symbol"])
            side = intent["side"]
            inventory_cost = position.average_cost if side == "SELL" else None
            if side == "SELL" and quantity > position.quantity:
                raise PaperOperationRejected("sell fill exceeds projected position")
            updated_order = apply_order_event(snapshot, event)
            journal = journal_fill(
                transaction_id=f"ledger:{fill_event_id}",
                order_id=order_id,
                fill_event_id=fill_event_id,
                side=side,
                quantity=quantity,
                price=price,
                fee=fee,
                inventory_unit_cost=inventory_cost,
            )
            updated_position = project_fill(
                position,
                PositionFill(fill_event_id, intent["symbol"], side, quantity, price),
            )

            connection.execute(
                "INSERT INTO paper_order_events "
                "(event_id, order_id, event_type, event_json, resulting_state, created_at) "
                "VALUES (?, ?, 'FILL', ?, ?, ?)",
                (fill_event_id, order_id, event_json, updated_order.state, now),
            )
            connection.execute(
                "UPDATE paper_order_snapshots SET snapshot_json = ?, state = ?, updated_at = ? "
                "WHERE order_id = ?",
                (self._snapshot_json(updated_order), updated_order.state, now, order_id),
            )
            self.fault_injector("after_fill_event")
            connection.execute(
                "INSERT INTO paper_ledger_transactions "
                "(transaction_id, order_id, fill_event_id, realized_pnl, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (journal.transaction_id, order_id, fill_event_id, str(journal.realized_pnl), now),
            )
            connection.executemany(
                "INSERT INTO paper_ledger_entries "
                "(transaction_id, account, debit, credit) VALUES (?, ?, ?, ?)",
                [
                    (journal.transaction_id, entry.account, str(entry.debit), str(entry.credit))
                    for entry in journal.entries
                ],
            )
            self.fault_injector("after_ledger")
            connection.execute(
                "INSERT INTO paper_v2_positions (symbol, snapshot_json, quantity, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                "snapshot_json = excluded.snapshot_json, quantity = excluded.quantity, "
                "updated_at = excluded.updated_at",
                (
                    updated_position.symbol,
                    self._position_json(updated_position),
                    str(updated_position.quantity),
                    now,
                ),
            )
            self.fault_injector("after_position")
        return updated_order, updated_position

    @staticmethod
    def _load_position(connection, symbol: str) -> PaperPosition:
        row = connection.execute(
            "SELECT snapshot_json FROM paper_v2_positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        if row is None:
            return empty_position(symbol)
        payload = json.loads(row["snapshot_json"])
        return PaperPosition(
            symbol=payload["symbol"],
            quantity=Decimal(payload["quantity"]),
            average_cost=Decimal(payload["average_cost"]),
            realized_pnl=Decimal(payload["realized_pnl"]),
            processed_fill_ids=tuple(payload["processed_fill_ids"]),
        )

    @staticmethod
    def _position_json(position: PaperPosition) -> str:
        return json.dumps(
            {
                "symbol": position.symbol,
                "quantity": str(position.quantity),
                "average_cost": str(position.average_cost),
                "realized_pnl": str(position.realized_pnl),
                "processed_fill_ids": list(position.processed_fill_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _request_json(intent: PaperOrderIntent) -> str:
        if not intent.idempotency_key.strip() or not intent.symbol.strip():
            raise ValueError("idempotency key and symbol are required")
        return json.dumps(
            {
                "order_id": intent.order_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "quantity": str(intent.quantity),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _snapshot_json(snapshot: PaperOrderSnapshot) -> str:
        return json.dumps(snapshot.to_record(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _event_json(event: PaperOrderEvent) -> str:
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
