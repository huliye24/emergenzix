"""Transactional orchestration boundary for PAPER v2 operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Literal

from .database import Database
from .paper_order_state import PaperOrderEvent, PaperOrderSnapshot, apply_order_event, new_paper_order
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
