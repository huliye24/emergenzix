"""Persistent, transition-based operational alerting for the paper-only watcher."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class OperationalTransition:
    """A stable incident transition ready to be placed in the notification outbox."""

    condition: str
    transition: str
    incident_id: str
    severity: str
    occurred_at: str
    message: str
    details: dict[str, Any]


def notification_health_conditions(
    summary: Mapping[str, Any], *, backlog_threshold: int
) -> dict[str, dict[str, Any]]:
    """Translate outbox metrics into active conditions; thresholds are inclusive."""
    if backlog_threshold < 1:
        raise ValueError("backlog_threshold must be at least one")

    pending = int(summary.get("pending", 0))
    dead = int(summary.get("dead", 0))
    if pending < 0 or dead < 0:
        raise ValueError("notification queue counts must not be negative")

    conditions: dict[str, dict[str, Any]] = {}
    if dead > 0:
        conditions["notification_dead_letters"] = {
            "severity": "error",
            "message": f"通知队列存在 {dead} 条死信，需要人工检查。",
            "recovery_message": "通知队列死信已清零，相关告警解除。",
            "details": {"dead_letter_count": dead},
        }
    if pending >= backlog_threshold:
        conditions["notification_backlog"] = {
            "severity": "warning",
            "message": (
                f"通知队列积压达到 {pending} 条，"
                f"告警阈值为 {backlog_threshold}。"
            ),
            "recovery_message": (
                f"通知队列积压已降至阈值 {backlog_threshold} 以下。"
            ),
            "details": {
                "pending_count_at_alert": pending,
                "backlog_threshold": backlog_threshold,
            },
        }
    return conditions


class OperationalAlertMonitor:
    """Persist incident transitions so alerts are deduplicated across restarts.

    State is committed before enqueueing a transition and marked as queued only
    after the callback succeeds. If enqueueing fails, the same transition key
    is retried on the next watcher cycle.
    """

    def __init__(self, state_path: str | Path) -> None:
        self.path = Path(state_path).expanduser()

    @staticmethod
    def _utc(value: datetime | None = None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("operational alert timestamps must be timezone-aware")
        return current.astimezone(timezone.utc)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("active"), dict):
            raise ValueError("operational alert state file has an invalid structure")
        active = payload["active"]
        for condition, record in active.items():
            if not isinstance(condition, str) or not isinstance(record, dict):
                raise ValueError("operational alert state file has an invalid incident")
        return active

    def _save(self, active: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"active": active}, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _transition(
        condition: str, record: dict[str, Any], transition: str
    ) -> OperationalTransition:
        details = dict(record["details"])
        details.update(
            {
                "alert_key": (
                    f"operational:{condition}:{record['incident_id']}:{transition}"
                ),
                "condition": condition,
                "incident_id": record["incident_id"],
                "transition": transition,
            }
        )
        recovered = transition == "recovered"
        return OperationalTransition(
            condition=condition,
            transition=transition,
            incident_id=record["incident_id"],
            severity="info" if recovered else record["severity"],
            occurred_at=record["recovered_at"] if recovered else record["opened_at"],
            message=record["recovery_message"] if recovered else record["message"],
            details=details,
        )

    @staticmethod
    def _normalize_condition(value: Mapping[str, Any]) -> dict[str, Any]:
        severity = str(value.get("severity", "warning"))
        if severity not in {"info", "warning", "error"}:
            raise ValueError("operational alert severity must be info, warning, or error")
        details = value.get("details", {})
        if not isinstance(details, dict):
            raise ValueError("operational alert details must be an object")
        message = str(value.get("message", "")).strip()
        recovery_message = str(value.get("recovery_message", "")).strip()
        if not message or not recovery_message:
            raise ValueError("operational alert messages must not be empty")
        return {
            "severity": severity,
            "message": message,
            "recovery_message": recovery_message,
            "details": details,
        }

    def reconcile(
        self,
        conditions: Mapping[str, Mapping[str, Any]],
        *,
        emit: Callable[[OperationalTransition], None],
        now: datetime | None = None,
    ) -> None:
        """Emit one alert on activation and one recovery message on resolution."""
        current_time = self._utc(now).isoformat()
        normalized = {
            str(name): self._normalize_condition(value)
            for name, value in conditions.items()
        }
        active = self._load()

        def queue_alert(condition: str, record: dict[str, Any]) -> None:
            if record.get("alert_queued", False):
                return
            emit(self._transition(condition, record, "active"))
            record["alert_queued"] = True
            self._save(active)

        # Finish incidents that have cleared, or whose recovery was interrupted.
        for condition in list(active):
            record = active[condition]
            if condition in normalized and not record.get("recovery_pending", False):
                queue_alert(condition, record)
                continue

            queue_alert(condition, record)
            if not record.get("recovery_pending", False):
                record["recovery_pending"] = True
                record["recovered_at"] = current_time
                self._save(active)
            emit(self._transition(condition, record, "recovered"))
            del active[condition]
            self._save(active)

        # Start newly observed incidents after prior recovery transitions are done.
        for condition, metadata in normalized.items():
            if condition in active:
                continue
            record = {
                **metadata,
                "incident_id": uuid.uuid4().hex,
                "opened_at": current_time,
                "alert_queued": False,
                "recovery_pending": False,
            }
            active[condition] = record
            self._save(active)
            queue_alert(condition, record)
