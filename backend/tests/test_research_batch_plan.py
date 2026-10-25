"""Acceptance tests for execution-free, deterministic weekly research plans."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.research_batch_plan import (
    BATCH_PLAN_VERSION,
    WeeklyResearchBatchPlanRequest,
    plan_weekly_research_batch,
)

URL = "/api/v1/research/date-range-batches/plan"
VALID = {
    "start_dates": ["2025-11-04", "2025-10-21", "2025-10-28"],
    "duration_days": 7, "training_days": 90, "initial_cash": 10000.0,
    "fee_bps": 10.0, "slippage_bps": 5.0,
}


def test_batch_plan_is_sorted_reproducible_and_no_side_effects(client) -> None:
    response = client.post(URL, json=VALID)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["plan_version"] == BATCH_PLAN_VERSION
    assert plan["backtest_protocol_version"] == "btc-date-range-backtest-v4"
    assert plan["periods_count"] == 3
    assert plan["plan_sha256"] == plan["batch_id"]
    assert len(plan["batch_id"]) == 64
    assert [p["backtest_request"]["start_date"] for p in plan["periods"]] == [
        "2025-10-21", "2025-10-28", "2025-11-04"
    ]
    assert len({p["planned_job_id"] for p in plan["periods"]}) == 3
    assert plan["periods"][0]["test_start_at"] == "2025-10-21T00:00:00Z"
    assert plan["periods"][0]["test_end_at_exclusive"] == "2025-10-28T00:00:00Z"
    assert plan["periods"][1]["test_start_at"] == plan["periods"][0]["test_end_at_exclusive"]
    assert plan["periods"][0]["required_training_candles"] == 540
    assert plan["periods"][0]["required_test_candles"] == 42
    assert plan["periods"][0]["total_required_candles"] == 582
    assert plan["selection"]["market"]["symbol"] == "BTC/USDT"
    assert plan["periods"][0]["saved_record_id"] is None
    assert all(not p["dataset_coverage_verified"] for p in plan["periods"])
    assert all(p["state"] == "planned_only" for p in plan["periods"])
    assert plan["execution_state"] == "not_started"
    assert plan["backtests_executed"] == 0
    assert plan["plan_persisted"] is False
    assert plan["orders_created"] is False
    assert plan["live_order_permission"] is False
    assert plan["paper_order_permission"] is False
    assert plan["testnet_order_permission"] is False
    assert plan["strategy_promotion_allowed"] is False

    reversed_dates = client.post(URL, json={
        **VALID, "start_dates": list(reversed(VALID["start_dates"]))
    })
    assert reversed_dates.status_code == 200
    assert reversed_dates.json() == plan
    with client.app.state.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM date_range_backtest_records").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


@pytest.mark.parametrize("dates", [
    ["2025-10-21", "2025-10-21"],
    ["2025-10-21", "2025-10-27"],
    ["2025-10-22", "2025-10-23"],
])
def test_batch_plan_rejects_duplicate_and_overlapping_test_dates(client, dates) -> None:
    assert client.post(URL, json={**VALID, "start_dates": dates}).status_code == 422


def test_batch_plan_rejects_future_and_unfinished_windows(client) -> None:
    assert client.post(URL, json={**VALID, "start_dates": ["2999-01-01"]}).status_code == 422
    today = datetime.now(timezone.utc).date().isoformat()
    assert client.post(URL, json={**VALID, "start_dates": [today]}).status_code == 422


@pytest.mark.parametrize("modification", [
    {"duration_days": 1},
    {"duration_days": 30},
    {"training_days": 59},
    {"training_days": 366},
    {"initial_cash": 0},
    {"fee_bps": -1},
    {"slippage_bps": 1001},
    {"unexpected": 3},
    {"start_dates": []},
    {"start_dates": ["2025-10-21T00:00:00Z"]},
    {"start_dates": [1761004800]},
    {"start_dates": ["2025/10/21"]},
    {"start_dates": ["2025-10-21"] * 13},
])
def test_batch_plan_rejects_bad_requests(client, modification) -> None:
    assert client.post(URL, json={**VALID, **modification}).status_code == 422


def test_batch_fingerprints_change_when_costs_or_training_change(client) -> None:
    baseline = client.post(URL, json=VALID).json()
    high_fees = client.post(URL, json={**VALID, "fee_bps": 20}).json()
    more_training = client.post(URL, json={**VALID, "training_days": 100}).json()
    assert baseline["batch_id"] != high_fees["batch_id"]
    assert baseline["batch_id"] != more_training["batch_id"]
    assert baseline["periods"][0]["planned_job_id"] != high_fees["periods"][0]["planned_job_id"]
    assert more_training["periods"][0]["required_training_candles"] == 600


def test_batch_plan_enforces_exclusive_end_time_and_timezone() -> None:
    request = WeeklyResearchBatchPlanRequest(start_dates=["2025-10-21"])
    end = datetime(2025, 10, 28, tzinfo=timezone.utc)
    assert plan_weekly_research_batch(request, now=end)["periods_count"] == 1
    with pytest.raises(ValueError, match="past"):
        plan_weekly_research_batch(request, now=end - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="timezone"):
        plan_weekly_research_batch(request, now=end.replace(tzinfo=None))


def test_no_execution_endpoint_is_exposed(client) -> None:
    assert client.get(URL).status_code == 405
    assert client.put(URL, json=VALID).status_code == 405
    assert client.post("/api/v1/research/date-range-batches/run", json=VALID).status_code == 404
