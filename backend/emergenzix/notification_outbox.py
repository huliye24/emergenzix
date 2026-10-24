"""Durable SQLite outbox for paper-only Telegram notifications."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .watch_agent import WatchEvent


@dataclass(frozen=True)
class DeliveryResult:
    notification_id: str
    status: str
    attempts: int
    error_type: str | None = None


class NotificationOutbox:
    """Persist notifications before delivery and retry failures with backoff."""

    def __init__(
        self,
        path: str | Path,
        *,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
        max_attempts: int = 8,
    ) -> None:
        if retry_base_seconds < 1 or retry_max_seconds < retry_base_seconds:
            raise ValueError("retry intervals must be positive and max >= base")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.path = str(path)
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.max_attempts = max_attempts
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    image BLOB,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'delivered', 'dead')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    next_attempt_at TEXT NOT NULL,
                    last_error_type TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending
                ON notification_outbox(status, next_attempt_at, created_at)
                """
            )

    @staticmethod
    def _utc(value: datetime | None = None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("outbox timestamps must be timezone-aware")
        return current.astimezone(timezone.utc)

    @staticmethod
    def _serialize_event(event: WatchEvent) -> str:
        return json.dumps(
            asdict(event), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @staticmethod
    def _dedupe_key(event: WatchEvent) -> str:
        details = event.details if isinstance(event.details, dict) else {}
        source_time = (
            details.get("bar_timestamp")
            or details.get("last_completed_at")
            or event.occurred_at
        )
        identity = {
            "event_type": event.event_type,
            "exchange": event.exchange,
            "symbol": event.symbol,
            "timeframe": event.timeframe,
            "source_time": source_time,
            "message": event.message,
            "details": details,
        }
        encoded = json.dumps(
            identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def enqueue(self, event: WatchEvent, *, image: bytes | None = None) -> str:
        """Insert once; repeated events for the same source candle reuse the ID."""
        notification_id = self._dedupe_key(event)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_outbox
                    (id, dedupe_key, event_json, image, status, attempts,
                     next_attempt_at, created_at)
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (
                    notification_id,
                    notification_id,
                    self._serialize_event(event),
                    image,
                    now,
                    now,
                ),
            )
            if image is not None:
                connection.execute(
                    """
                    UPDATE notification_outbox SET image = ?
                    WHERE id = ? AND status = 'pending' AND image IS NULL
                    """,
                    (image, notification_id),
                )
        return notification_id

    def get_status(self, notification_id: str) -> DeliveryResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, status, attempts, last_error_type FROM notification_outbox WHERE id = ?",
                (notification_id,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryResult(
            notification_id=row["id"],
            status=row["status"],
            attempts=int(row["attempts"]),
            error_type=row["last_error_type"],
        )

    def pending_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_outbox WHERE status = 'pending'"
            ).fetchone()
        return int(row["count"])

    def deliver_one(
        self,
        notifier: Any,
        notification_id: str,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> DeliveryResult:
        """Attempt one notification; never include provider error text in stored logs."""
        current = self._utc(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE id = ?",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise KeyError("notification does not exist")
        if row["status"] != "pending":
            return DeliveryResult(
                notification_id=row["id"],
                status=row["status"],
                attempts=int(row["attempts"]),
                error_type=row["last_error_type"],
            )
        next_attempt = datetime.fromisoformat(row["next_attempt_at"])
        if not force and next_attempt > current:
            return DeliveryResult(
                notification_id=row["id"],
                status="deferred",
                attempts=int(row["attempts"]),
                error_type=row["last_error_type"],
            )

        event = WatchEvent(**json.loads(row["event_json"]))
        image = bytes(row["image"]) if row["image"] is not None else None
        try:
            notifier.send(event, image=image)
        except Exception as exc:
            attempts = int(row["attempts"]) + 1
            delay = min(
                self.retry_base_seconds * (2 ** min(attempts - 1, 20)),
                self.retry_max_seconds,
            )
            status = "dead" if attempts >= self.max_attempts else "pending"
            next_at = (current + timedelta(seconds=delay)).isoformat()
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET attempts = ?, status = ?, next_attempt_at = ?,
                        last_error_type = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (attempts, status, next_at, type(exc).__name__, notification_id),
                )
            return DeliveryResult(
                notification_id=notification_id,
                status=status,
                attempts=attempts,
                error_type=type(exc).__name__,
            )

        attempts = int(row["attempts"]) + 1
        delivered_at = current.isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notification_outbox
                SET attempts = ?, status = 'delivered', delivered_at = ?,
                    last_error_type = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (attempts, delivered_at, notification_id),
            )
        return DeliveryResult(
            notification_id=notification_id,
            status="delivered",
            attempts=attempts,
        )

    def deliver_pending(
        self,
        notifier: Any,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[DeliveryResult]:
        """Attempt due messages in order; a bad message does not block later messages."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        current = self._utc(now)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM notification_outbox
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (current.isoformat(), limit),
            ).fetchall()
        return [
            self.deliver_one(notifier, row["id"], now=current)
            for row in rows
        ]
