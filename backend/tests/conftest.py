from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from emergenzix_api.main import create_app


@pytest.fixture
def client(tmp_path: pytest.TempPathFactory):
    app = create_app(
        db_path=tmp_path / "emergenzix-test.db",
        initial_paper_cash=10_000.0,
        max_order_notional=2_000.0,
        paper_fee_bps=10.0,
    )
    with TestClient(app) as test_client:
        yield test_client
