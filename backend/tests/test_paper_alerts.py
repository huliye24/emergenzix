import json
import sqlite3

from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.paper_alerts import enqueue_paper_execution_alert


def test_execution_alert_is_durable_and_deduplicated(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    outbox = NotificationOutbox(path)
    first = enqueue_paper_execution_alert(
        outbox,
        alert_key="paper:reconcile:incident-1",
        condition="reconciliation_mismatch",
        message="PAPER账户对账不一致，已冻结新开仓",
        details={"symbol": "BTCUSDT", "discrepancy_count": 2},
    )
    second = enqueue_paper_execution_alert(
        outbox,
        alert_key="paper:reconcile:incident-1",
        condition="reconciliation_mismatch",
        message="PAPER账户对账不一致，已冻结新开仓",
        details={"symbol": "BTCUSDT", "discrepancy_count": 2},
    )
    assert first == second
    assert NotificationOutbox(path).pending_count() == 1

    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute("SELECT event_json FROM notification_outbox").fetchone()[0]
        )
    assert payload["event_type"] == "paper_execution_alert"
    assert payload["details"]["condition"] == "reconciliation_mismatch"


def test_execution_alert_rejects_missing_identity(tmp_path):
    outbox = NotificationOutbox(tmp_path / "outbox.sqlite3")
    try:
        enqueue_paper_execution_alert(
            outbox, alert_key="", condition="unknown_order", message="alert"
        )
    except ValueError as error:
        assert "required" in str(error)
    else:
        raise AssertionError("missing alert identity must fail closed")
