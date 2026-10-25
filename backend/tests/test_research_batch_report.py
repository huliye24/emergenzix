"""Read-only batch report acceptance: provenance, partial status, and export."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.research_batch_plan import (
    WeeklyResearchBatchPlanRequest, plan_weekly_research_batch,
)
from emergenzix_api.research_batch_report import (
    BatchReportError, build_research_batch_report,
    build_research_batch_report_from_file,
    render_research_batch_markdown, write_batch_report,
)
from emergenzix_api.research_batch_report_cli import main as report_cli_main
from emergenzix_api.research_batch_state import (
    claim_next_research_job, fail_research_job, get_research_batch,
    register_research_batch,
)
from emergenzix_api.research_batch_worker import run_one_batch_job
from emergenzix_api.schemas import Bar

CLOCK = datetime(2025, 12, 1, tzinfo=timezone.utc)


def _batch(tmp_path, dates=("2025-10-21", "2025-10-28")):
    db = Database(str(tmp_path / "batch.db"), initial_paper_cash=10000)
    plan = plan_weekly_research_batch(
        WeeklyResearchBatchPlanRequest(start_dates=list(dates)), now=CLOCK,
    )
    register_research_batch(db, plan, now=CLOCK)
    return db, plan


def _archive(monkeypatch):
    import emergenzix_api.date_range_backtest as engine

    def offline_history(*, start_at, end_at):
        n = int((end_at - start_at).total_seconds() // (4 * 3600)) + 1
        candles = []
        for index in range(n):
            close = 80000 + 2500 * math.sin(index * 0.11) + index * 3
            opening = close * (1 + 0.0005 * math.sin(index * 0.07))
            candles.append(Bar(
                timestamp=start_at + timedelta(hours=4 * index),
                open=opening, high=max(opening, close) * 1.003,
                low=min(opening, close) * 0.997,
                close=close, volume=1000 + index,
            ).model_dump(mode="json"))
        return candles

    monkeypatch.setattr(engine, "fetch_binance_btc_4h_archive", offline_history)


def test_report_before_execution_has_no_fake_financial_results(tmp_path) -> None:
    db, plan = _batch(tmp_path)
    before = db.path
    report = build_research_batch_report(db, plan["batch_id"], now=CLOCK)
    again = build_research_batch_report(Database(before), plan["batch_id"], now=CLOCK)
    assert report == again
    assert report["total_planned"] == 2
    assert report["state"] == "in_progress"
    assert report["state_counts"] == {
        "pending": 2, "leased": 0, "completed": 0, "failed": 0, "cancelled": 0
    }
    assert report["completion_rate_pct"] == 0
    assert report["completed_financial_observations"] == 0
    assert report["individual_net_return_distribution_pct"] is None
    assert report["individual_max_drawdown_distribution_pct"] is None
    assert report["compounded_portfolio_return_pct"] is None
    assert report["portfolio_max_drawdown_pct"] is None
    assert "lease_token" not in json.dumps(report)
    assert all(row["result"] is None for row in report["jobs"])
    assert report["orders_created"] is False
    assert report["paper_order_permission"] is False
    assert report["testnet_order_permission"] is False
    assert report["live_order_permission"] is False
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM date_range_backtest_records").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_report_verifies_one_completed_job_and_excludes_pending(
    tmp_path, monkeypatch,
) -> None:
    _archive(monkeypatch)
    db, plan = _batch(tmp_path)
    empty = build_research_batch_report(db, plan["batch_id"])
    completed = run_one_batch_job(db, plan["batch_id"], worker_id="worker")
    assert completed["status"] == "completed"
    report = build_research_batch_report(db, plan["batch_id"])
    assert report["snapshot_sha256"] != empty["snapshot_sha256"]
    assert report["total_planned"] == 2
    assert report["state_counts"]["completed"] == 1
    assert report["state_counts"]["pending"] == 1
    assert report["completion_rate_pct"] == 50
    assert report["completed_financial_observations"] == 1
    assert (
        report["positive_completed_observations"]
        + report["negative_completed_observations"]
        + report["flat_completed_observations"] == 1
    )
    assert report["individual_net_return_distribution_pct"] is not None
    assert report["individual_max_drawdown_distribution_pct"] is not None
    assert report["jobs"][0]["saved_record_id"] == completed["record_id"]
    assert report["jobs"][0]["saved_record_sha256"] == completed["record_sha256"]
    assert report["jobs"][1]["result"] is None
    assert report["different_selected_candidates"] is False
    assert report["financial_aggregation_type"] == (
        "completed_independent_saved_observations_only"
    )
    md = render_research_batch_markdown(report)
    assert "Completion rate: 50.00%" in md
    assert completed["record_id"] in md
    assert "cannot" not in md or "Do not compound" in md
    assert "No portfolio return is compounded" in md
    assert "lease_token" not in md
    saved = write_batch_report(report, tmp_path / "outputs" / "batch.json")
    loaded = json.loads((tmp_path / "outputs" / "batch.json").read_text())
    assert loaded == report
    assert "markdown" in saved
    assert (tmp_path / "outputs" / "batch.md").exists()
    with pytest.raises(BatchReportError):
        write_batch_report(report, tmp_path / "bad.txt")


def test_failure_retries_are_reported_not_counted_as_losing_weeks(tmp_path) -> None:
    db, plan = _batch(tmp_path, dates=("2025-10-21",))
    for _ in range(3):
        claimed = claim_next_research_job(
            db, plan["batch_id"], worker_id="worker", now=CLOCK
        )
        result = fail_research_job(
            db, claimed["planned_job_id"], claimed["lease_token"],
            error_code="data_incomplete", now=CLOCK,
        )
    assert result == "failed"
    report = build_research_batch_report(db, plan["batch_id"], now=CLOCK)
    assert report["state"] == "finished_with_incomplete_jobs"
    assert report["state_counts"]["failed"] == 1
    assert report["failure_reason_counts"] == {"data_incomplete": 1}
    assert report["completion_rate_pct"] == 0
    assert report["negative_completed_observations"] == 0
    assert report["individual_net_return_distribution_pct"] is None
    assert report["jobs"][0]["attempts"] == 3
    assert report["jobs"][0]["error_code"] == "data_incomplete"
    assert "No completed, verified" in render_research_batch_markdown(report)


def test_report_exposes_expired_lease_as_warning_without_reclaiming(tmp_path) -> None:
    db, plan = _batch(tmp_path, dates=("2025-10-21",))
    lease = claim_next_research_job(
        db, plan["batch_id"], worker_id="worker",
        now=CLOCK, lease_seconds=30,
    )
    report = build_research_batch_report(
        db, plan["batch_id"], now=CLOCK + timedelta(minutes=1),
    )
    assert report["state_counts"]["leased"] == 1
    assert report["jobs"][0]["lease_expired_at_snapshot"] is True
    assert any("expired" in warning for warning in report["warnings"])
    assert "lease_token" not in json.dumps(report)
    assert get_research_batch(db, plan["batch_id"])["jobs"][0]["state"] == "leased"


def test_tampering_completed_job_reference_fails_entire_batch(tmp_path, monkeypatch) -> None:
    _archive(monkeypatch)
    db, plan = _batch(tmp_path, dates=("2025-10-21",))
    assert run_one_batch_job(db, plan["batch_id"], worker_id="worker")["status"] == "completed"
    with db.connection() as conn:
        conn.execute(
            "UPDATE research_batch_jobs SET saved_record_sha256 = ? WHERE batch_id = ?",
            ("f" * 64, plan["batch_id"]),
        )
    with pytest.raises(BatchReportError, match="integrity"):
        build_research_batch_report(db, plan["batch_id"])


def test_report_rejects_mismatched_job_request_or_bad_batch_id(tmp_path) -> None:
    db, plan = _batch(tmp_path)
    with pytest.raises(BatchReportError, match="invalid batch"):
        build_research_batch_report(db, "../../file")
    with db.connection() as conn:
        conn.execute(
            "UPDATE research_batch_jobs SET request_json = ? "
            "WHERE id = ?", ('{"start_date":"2000-01-01"}', plan["periods"][0]["planned_job_id"]),
        )
    with pytest.raises(BatchReportError, match="approved plan"):
        build_research_batch_report(db, plan["batch_id"])


def test_private_report_cli_exports_json_and_markdown(tmp_path, capsys) -> None:
    db, plan = _batch(tmp_path)
    output = tmp_path / "export" / "report.json"
    assert report_cli_main([
        "--batch-id", plan["batch_id"], "--db-path", db.path,
        "--output", str(output),
    ]) == 0
    message = json.loads(capsys.readouterr().out)
    assert message["status"] == "ok"
    assert message["snapshot_sha256"]
    assert output.exists()
    assert output.with_suffix(".md").exists()
    assert report_cli_main([
        "--batch-id", "bad", "--db-path", db.path,
    ]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == (
        "batch_report_unavailable"
    )


def test_three_historical_weeks_complete_end_to_end_across_restart(
    tmp_path, monkeypatch,
) -> None:
    """One real engine call per week, immutable DB, then exact report snapshot."""
    _archive(monkeypatch)
    db, plan = _batch(
        tmp_path, dates=("2025-10-21", "2025-10-28", "2025-11-04"),
    )
    initial = build_research_batch_report_from_file(db.path, plan["batch_id"], now=CLOCK)
    assert initial["total_planned"] == 3
    assert initial["completed_financial_observations"] == 0

    first = run_one_batch_job(db, plan["batch_id"], worker_id="first")
    assert first["status"] == "completed"
    assert first["record_id"]
    # Emulate the cloud process restarting between tasks.
    db = Database(db.path)
    second = run_one_batch_job(db, plan["batch_id"], worker_id="second")
    assert second["status"] == "completed"
    db = Database(db.path)
    third = run_one_batch_job(db, plan["batch_id"], worker_id="third")
    assert third["status"] == "completed"
    assert len({first["record_id"], second["record_id"], third["record_id"]}) == 3

    complete = build_research_batch_report_from_file(
        db.path, plan["batch_id"], now=CLOCK,
    )
    assert complete["state"] == "completed"
    assert complete["state_counts"] == {
        "pending": 0, "leased": 0, "completed": 3, "failed": 0, "cancelled": 0,
    }
    assert complete["completion_rate_pct"] == 100
    assert complete["completed_financial_observations"] == 3
    assert complete["positive_completed_observations"] + complete[
        "negative_completed_observations"
    ] + complete["flat_completed_observations"] == 3
    assert [item["test_start_at"][:10] for item in complete["jobs"]] == [
        "2025-10-21", "2025-10-28", "2025-11-04",
    ]
    assert all(j["state"] == "completed" and j["saved_record_sha256"] for j in complete["jobs"])
    assert complete["snapshot_sha256"] != initial["snapshot_sha256"]
    assert complete["compounded_portfolio_return_pct"] is None
    assert complete["portfolio_max_drawdown_pct"] is None
    assert complete["live_order_permission"] is False
    assert complete["orders_created"] is False
    assert run_one_batch_job(db, plan["batch_id"], worker_id="fourth")["status"] == "idle"
    assert build_research_batch_report_from_file(
        db.path, plan["batch_id"], now=CLOCK,
    ) == complete

    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM date_range_backtest_records"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_orders"
        ).fetchone()[0] == 0
    exported = write_batch_report(complete, tmp_path / "out" / "batch.json")
    assert exported["json"].endswith(".json")
    assert "Completion rate: 100.00%" in (
        tmp_path / "out" / "batch.md"
    ).read_text(encoding="utf-8")


def test_report_only_cli_does_not_create_database_or_directories(
    tmp_path, capsys,
) -> None:
    """Regression: Database(...) previously initialized a missing sqlite file."""
    missing = tmp_path / "no-such-folder" / "missing.db"
    result = report_cli_main([
        "--batch-id", "a" * 64, "--db-path", str(missing),
    ])
    assert result == 2
    assert not missing.exists()
    assert not missing.parent.exists()
    assert json.loads(capsys.readouterr().err)["error_code"] == (
        "batch_report_unavailable"
    )


def test_report_only_cli_skips_writable_database_initializer(
    tmp_path, monkeypatch, capsys,
) -> None:
    db, plan = _batch(tmp_path)
    from emergenzix_api.database import Database as Storage

    def deny_init(*args, **kwargs):
        raise AssertionError("Report CLI must not initialize or migrate a database")

    monkeypatch.setattr(Storage, "initialize", deny_init)
    assert report_cli_main([
        "--batch-id", plan["batch_id"], "--db-path", db.path,
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["state_counts"]["pending"] == 2


def test_read_only_report_rejects_tampered_completed_record(
    tmp_path, monkeypatch,
) -> None:
    _archive(monkeypatch)
    db, plan = _batch(tmp_path, dates=("2025-10-21",))
    result = run_one_batch_job(db, plan["batch_id"], worker_id="worker")
    assert result["status"] == "completed"
    with db.connection() as conn:
        conn.execute(
            "UPDATE research_batch_jobs SET saved_record_sha256 = ? "
            "WHERE batch_id = ?", ("f" * 64, plan["batch_id"]),
        )
    with pytest.raises(BatchReportError, match="integrity"):
        build_research_batch_report_from_file(db.path, plan["batch_id"])
