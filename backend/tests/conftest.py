from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from emergenzix_api.main import create_app
from emergenzix_api.paper_kill_switch import get_paper_kill_switch, set_paper_kill_switch


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "emergenzix-test.db",
        notification_db_path=tmp_path / "notification-outbox.sqlite3",
        initial_paper_cash=10_000.0,
        max_order_notional=2_000.0,
        paper_fee_bps=10.0,
    )
    control = get_paper_kill_switch(app.state.database)
    set_paper_kill_switch(
        app.state.database,
        paused=False,
        reason="test fixture explicitly enables PAPER order creation",
        expected_revision=control.revision,
    )
    with TestClient(app) as test_client:
        yield test_client
