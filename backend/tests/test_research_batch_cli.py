"""Operator batch CLI: dry-run safety, idempotency, read-only status, no orders."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.research_batch_cli import main as batch_cli_main
from emergenzix_api.research_batch_state import claim_next_research_job

WEEKS = ("2025-10-21", "2025-10-28", "2025-11-04")


def _args(command: str, *dates: str) -> list[str]:
    output = [command]
    for day in dates:
        output.extend(["--start-date", day])
    return output


def _read(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_plan_is_pure_no_settings_or_database_side_effects(
    tmp_path, monkeypatch, capsys,
) -> None:
    import emergenzix_api.research_batch_cli as cli

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run planning must never access runtime config or DB")

    monkeypatch.setattr(cli.Settings, "from_env", forbidden)
    monkeypatch.setattr(cli.Database, "__init__", forbidden)
    first = _args("plan", *reversed(WEEKS))
    assert batch_cli_main(first) == 0
    first_report = _read(capsys)
    assert first_report["status"] == "planned_only"
    assert first_report["periods_count"] == 3
    assert first_report["plan_persisted"] is False
    assert first_report["orders_created"] is False
    assert [p["start_date"] for p in first_report["periods"]] == list(WEEKS)

    assert batch_cli_main(_args("plan", *WEEKS)) == 0
    sorted_report = _read(capsys)
    assert sorted_report == first_report
    assert not (tmp_path / "emergenzix.db").exists()


def test_register_is_idempotent_then_status_uses_readonly_snapshot(
    tmp_path, capsys, monkeypatch,
) -> None:
    dbpath = tmp_path / "records" / "emergenzix.db"
    args = [*_args("register", *WEEKS), "--db-path", str(dbpath)]
    assert batch_cli_main(args) == 0
    registered = _read(capsys)
    assert registered["status"] == "registered_or_already_present"
    assert registered["job_count"] == 3
    assert [job["state"] for job in registered["jobs"]] == ["pending"] * 3
    batch_id = registered["batch_id"]

    # Duplicate registration must NOT reset a leased job's attempt counter.
    db = Database(str(dbpath))
    leased = claim_next_research_job(db, batch_id, worker_id="operator")
    assert leased is not None
    assert leased["sequence"] == 1
    assert leased["attempt"] == 1
    assert batch_cli_main(args) == 0
    duplicate = _read(capsys)
    assert duplicate["batch_id"] == batch_id
    assert duplicate["jobs"][0]["state"] == "leased"
    assert duplicate["jobs"][0]["attempts"] == 1
    assert duplicate["jobs"][1]["state"] == "pending"

    def forbidden(*args, **kwargs):
        raise AssertionError("status must not initialize/migrate the SQLite file")

    monkeypatch.setattr(Database, "initialize", forbidden)
    command = [
        "status", "--db-path", str(dbpath), "--batch-id", batch_id,
    ]
    assert batch_cli_main(command) == 0
    view = _read(capsys)
    assert view["status"] == "in_progress"
    assert view["state_counts"]["leased"] == 1
    assert view["state_counts"]["pending"] == 2
    assert view["state_counts"]["completed"] == 0
    assert view["jobs"][0]["attempts"] == 1
    assert view["orders_created"] is False
    assert view["live_order_permission"] is False
    assert "lease_token" not in json.dumps(view)
    with db.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_batch_plans"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM research_batch_jobs"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM date_range_backtest_records"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_orders"
        ).fetchone()[0] == 0


def test_register_invalid_week_never_initializes_a_database(
    tmp_path, capsys,
) -> None:
    dbpath = tmp_path / "would-be-created" / "state.db"
    invalid = [
        *_args("register", "2025-10-21", "2025-10-25"),
        "--db-path", str(dbpath),
    ]
    assert batch_cli_main(invalid) == 2
    error = json.loads(capsys.readouterr().err)
    assert error == {
        "status": "error",
        "error_code": "invalid_batch_request",
    }
    assert not dbpath.exists()
    assert not dbpath.parent.exists()


@pytest.mark.parametrize("dates", [
    ("2025-10-21", "2025-10-21"),
    ("2025-10-21", "2025-10-22"),
    ("2999-10-21",),
    ("2025-10-21T00:00:00Z",),
    ("2025/10/21",),
    ("2025-10-21",) * 13,
])
def test_plan_rejects_duplicate_future_overlaps_and_malformed_dates(
    dates, capsys,
) -> None:
    assert batch_cli_main(_args("plan", *dates)) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "invalid_batch_request"


def test_missing_status_database_remains_missing(
    tmp_path, capsys,
) -> None:
    unknown = tmp_path / "missing" / "db.sqlite3"
    assert batch_cli_main([
        "status", "--db-path", str(unknown), "--batch-id", "a" * 64,
    ]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == (
        "batch_status_unavailable"
    )
    assert not unknown.exists()
    assert not unknown.parent.exists()


def test_operator_cli_never_adds_hidden_execution_mode(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        batch_cli_main(_args("plan", "2025-10-21") + ["--execute-one"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
