from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from emergenzix_api.operational_alerts import (
    OperationalAlertMonitor,
    notification_health_conditions,
)


def _condition(*, pending: int = 20, threshold: int = 20) -> dict[str, dict[str, object]]:
    return notification_health_conditions(
        {"pending": pending, "dead": 0},
        backlog_threshold=threshold,
    )


def test_health_conditions_trigger_at_threshold_and_on_dead_letters() -> None:
    conditions = notification_health_conditions(
        {"pending": 20, "dead": 2},
        backlog_threshold=20,
    )
    assert set(conditions) == {"notification_backlog", "notification_dead_letters"}
    assert conditions["notification_backlog"]["details"]["pending_count_at_alert"] == 20
    assert conditions["notification_dead_letters"]["severity"] == "error"


def test_monitor_deduplicates_active_incident_and_emits_one_recovery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ops-state.json"
    monitor = OperationalAlertMonitor(path)
    first_time = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    emitted = []

    monitor.reconcile(_condition(), emit=emitted.append, now=first_time)
    monitor.reconcile(
        _condition(),
        emit=emitted.append,
        now=first_time + timedelta(minutes=5),
    )
    assert [item.transition for item in emitted] == ["active"]

    monitor.reconcile(
        _condition(pending=19),
        emit=emitted.append,
        now=first_time + timedelta(minutes=10),
    )
    monitor.reconcile(
        _condition(pending=19),
        emit=emitted.append,
        now=first_time + timedelta(minutes=15),
    )
    assert [item.transition for item in emitted] == ["active", "recovered"]

    monitor.reconcile(
        _condition(),
        emit=emitted.append,
        now=first_time + timedelta(minutes=20),
    )
    assert [item.transition for item in emitted] == ["active", "recovered", "active"]
    assert emitted[0].incident_id != emitted[2].incident_id


def test_failed_enqueue_retries_same_transition_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "ops-state.json"
    monitor = OperationalAlertMonitor(path)
    first_time = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    failed_transitions = []

    def fail(_transition):
        raise RuntimeError("simulated outbox write failure")

    with pytest.raises(RuntimeError, match="outbox write failure"):
        monitor.reconcile(_condition(), emit=fail, now=first_time)

    restarted = OperationalAlertMonitor(path)
    retried = []
    restarted.reconcile(
        _condition(),
        emit=retried.append,
        now=first_time + timedelta(minutes=5),
    )
    assert len(retried) == 1
    assert retried[0].details["alert_key"] == (
        "operational:notification_backlog:"
        + retried[0].incident_id
        + ":active"
    )
    assert retried[0].occurred_at == first_time.isoformat()
