"""Persistent operator control for blocking new PAPER exposure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .database import Database


@dataclass(frozen=True, slots=True)
class PaperKillSwitch:
    new_orders_paused: bool
    reason: str
    revision: int
    updated_at: str
    allow_cancel_orders: bool = True
    allow_reduce_risk: bool = True


def get_paper_kill_switch(database: Database) -> PaperKillSwitch:
    with database.connection() as connection:
        row = connection.execute(
            "SELECT * FROM paper_operational_control WHERE id = 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("paper operational control is not initialized")
    return PaperKillSwitch(
        new_orders_paused=bool(row["new_orders_paused"]),
        reason=row["reason"],
        revision=int(row["revision"]),
        updated_at=row["updated_at"],
    )


def set_paper_kill_switch(
    database: Database,
    *,
    paused: bool,
    reason: str,
    expected_revision: int,
) -> PaperKillSwitch:
    if not isinstance(paused, bool):
        raise ValueError("paused must be boolean")
    normalized_reason = reason.strip()
    if paused and not normalized_reason:
        raise ValueError("a reason is required when pausing new orders")
    if expected_revision < 0 or isinstance(expected_revision, bool):
        raise ValueError("expected_revision must be a non-negative integer")
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            "UPDATE paper_operational_control SET new_orders_paused = ?, reason = ?, "
            "revision = revision + 1, updated_at = ? WHERE id = 1 AND revision = ?",
            (int(paused), normalized_reason, now, expected_revision),
        ).rowcount
        if changed != 1:
            raise ValueError("kill switch revision conflict")
    return get_paper_kill_switch(database)
