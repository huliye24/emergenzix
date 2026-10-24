from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from emergenzix_api.database import Database
from emergenzix_api.date_range_compare_cli import main
from emergenzix_api.date_range_comparison import (
    DateRangeComparisonError,
    compare_saved_date_range_records,
)


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _record(
    record_id: str,
    *,
    start_at: str = "2025-10-21T00:00:00Z",
    fee_bps: float = 10.0,
    corrupt: bool = False,
    unsupported: bool = False,
) -> tuple[dict[str, object], str]:
    record: dict[str, object] = {
        "id": record_id,
        "record_type": "btc_date_range_backtest",
        "created_at": "2026-10-24T00:00:00Z",
        "protocol_version": "btc-date-range-backtest-v4",
        "dataset": {"sha256": "d" * 64},
        "configuration": {"fee_bps": fee_bps, "slippage_bps": 5.0},
        "report": {
            "period": {
                "test_start_at": start_at,
                "test_end_at_exclusive": "2025-10-28T00:00:00Z",
            },
            "strategy_selection": {
                "selected_candidate": {
                    "candidate_id": "sma-3-10",
                    "strategy_type": "sma_crossover",
                }
            },
            "test_result": {
                "total_return_pct": 0.5,
                "benchmark_return_pct": 3.2,
                "max_drawdown_pct": 3.4,
                "number_of_trades": 2,
                "win_rate_pct": 50.0,
                "total_fees": 39.3,
            },
        },
    }
    if unsupported:
        del record["report"]
    integrity = {key: value for key, value in record.items() if key != "created_at"}
    fingerprint = hashlib.sha256(_canonical_json(integrity).encode("utf-8")).hexdigest()
    return record, ("0" * 64 if corrupt else fingerprint)


def _database(tmp_path: Path, *records: tuple[dict[str, object], str]) -> Path:
    path = tmp_path / "comparison.db"
    Database(str(path), initial_paper_cash=10_000.0)
    with sqlite3.connect(path) as connection:
        for record, fingerprint in records:
            configuration = record.get("configuration", {})
            report = record.get("report", {})
            period = report.get("period", {}) if isinstance(report, dict) else {}
            connection.execute(
                """
                INSERT INTO date_range_backtest_records
                    (id, dataset_id, dataset_sha256, start_at, end_at,
                     duration_days, training_days, protocol_version,
                     record_sha256, record_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    "d" * 64,
                    "d" * 64,
                    period.get("test_start_at", "2025-10-21T00:00:00Z"),
                    period.get("test_end_at_exclusive", "2025-10-28T00:00:00Z"),
                    7,
                    90,
                    record["protocol_version"],
                    fingerprint,
                    _canonical_json(record),
                    record["created_at"],
                ),
            )
    return path


def test_compare_valid_records_preserves_order_and_marks_observations(tmp_path: Path) -> None:
    first = _record("a" * 64)
    second = _record("b" * 64, start_at="2025-11-01T00:00:00Z", fee_bps=20.0)
    path = _database(tmp_path, first, second)

    comparison = compare_saved_date_range_records(path, ["b" * 64, "a" * 64])

    assert [row["record_id"] for row in comparison["observations"]] == [
        "b" * 64,
        "a" * 64,
    ]
    assert comparison["controlled_head_to_head"] is False
    assert comparison["different_test_periods"] is True
    assert comparison["different_cost_assumptions"] is True
    assert comparison["automatic_winner_selection"] is False
    assert comparison["observations"][0]["protocol_version"] == (
        "btc-date-range-backtest-v4"
    )
    assert comparison["observations"][0]["dataset_sha256"] == "d" * 64


def test_compare_rejects_missing_corrupt_and_unsupported_records(tmp_path: Path) -> None:
    valid = _record("a" * 64)
    corrupt = _record("b" * 64, corrupt=True)
    unsupported = _record("c" * 64, unsupported=True)
    path = _database(tmp_path, valid, corrupt, unsupported)

    with pytest.raises(DateRangeComparisonError, match="not found"):
        compare_saved_date_range_records(path, ["a" * 64, "f" * 64])
    with pytest.raises(DateRangeComparisonError, match="integrity fingerprint"):
        compare_saved_date_range_records(path, ["a" * 64, "b" * 64])
    with pytest.raises(DateRangeComparisonError, match="unsupported.*shape"):
        compare_saved_date_range_records(path, ["a" * 64, "c" * 64])


def test_compare_cli_prints_table_and_writes_csv_without_mutating_db(
    tmp_path: Path, capsys
) -> None:
    first = _record("a" * 64)
    second = _record("b" * 64)
    path = _database(tmp_path, first, second)
    output = tmp_path / "comparison.csv"
    before = path.read_bytes()

    status = main(
        ["a" * 64, "b" * 64, "--db-path", str(path), "--output", str(output)]
    )

    assert status == 0
    assert path.read_bytes() == before
    stdout = capsys.readouterr().out
    assert "protocol_version" in stdout
    assert "independent saved observations" in stdout
    assert "no winner is selected" in stdout
    csv_text = output.read_text(encoding="utf-8-sig")
    assert len(csv_text.splitlines()) == 3
    assert "dataset_sha256" in csv_text
    assert "btc-date-range-backtest-v4" in csv_text


# API contract for saved research observations. These calls must never rerun the backtest.
from fastapi.testclient import TestClient

from emergenzix_api.main import create_app


_COMPARISON_API = "/api/v1/research/date-range-comparisons"


def _client_for_records(tmp_path: Path, *records: tuple[dict[str, object], str]) -> TestClient:
    db_path = _database(tmp_path, *records)
    return TestClient(
        create_app(
            db_path=db_path,
            notification_db_path=tmp_path / "notification-outbox.sqlite3",
        )
    )


def test_comparison_api_preserves_id_order_and_stays_read_only(tmp_path: Path) -> None:
    first = _record("a" * 64)
    second = _record("b" * 64, start_at="2025-11-01T00:00:00Z", fee_bps=20)
    client = _client_for_records(tmp_path, first, second)
    path = tmp_path / "comparison.db"
    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT id, record_json, record_sha256 FROM date_range_backtest_records ORDER BY id"
        ).fetchall()

    response = client.get(
        _COMPARISON_API,
        params=[("record_ids", "b" * 64), ("record_ids", "a" * 64)],
    )

    assert response.status_code == 200
    result = response.json()
    assert [item["record_id"] for item in result["observations"]] == [
        "b" * 64,
        "a" * 64,
    ]
    assert result["comparison_type"] == "independent_saved_observations"
    assert result["controlled_head_to_head"] is False
    assert result["automatic_winner_selection"] is False
    assert result["different_test_periods"] is True
    assert result["different_cost_assumptions"] is True
    assert result["observations"][0]["dataset_sha256"] == "d" * 64
    with sqlite3.connect(path) as connection:
        after = connection.execute(
            "SELECT id, record_json, record_sha256 FROM date_range_backtest_records ORDER BY id"
        ).fetchall()
    assert after == before
    assert client.post(_COMPARISON_API, json={"record_ids": ["a" * 64, "b" * 64]}).status_code == 405


@pytest.mark.parametrize(
    "record_ids",
    [
        ["a" * 64],
        ["a" * 64, "a" * 64],
        ["a" * 64, "B" * 64],
        ["a" * 64, "../etc/passwd"],
        ["a" * 64, "b" * 64] * 6,
    ],
)
def test_comparison_api_rejects_invalid_inputs_without_disclosing_records(
    tmp_path: Path, record_ids: list[str]
) -> None:
    client = _client_for_records(tmp_path, _record("a" * 64), _record("b" * 64))
    response = client.get(
        _COMPARISON_API,
        params=[("record_ids", record_id) for record_id in record_ids],
    )
    assert response.status_code == 422
    assert "record_json" not in response.text


def test_comparison_api_missing_id_returns_404(tmp_path: Path) -> None:
    client = _client_for_records(tmp_path, _record("a" * 64))
    response = client.get(
        _COMPARISON_API,
        params=[("record_ids", "a" * 64), ("record_ids", "f" * 64)],
    )
    assert response.status_code == 404
    assert response.json() == {
        "detail": "One or more saved backtest records were not found"
    }


@pytest.mark.parametrize(
    "damaged",
    [
        _record("b" * 64, corrupt=True),
        _record("b" * 64, unsupported=True),
    ],
)
def test_comparison_api_fails_closed_for_bad_records(
    tmp_path: Path, damaged: tuple[dict[str, object], str]
) -> None:
    client = _client_for_records(tmp_path, _record("a" * 64), damaged)
    response = client.get(
        _COMPARISON_API,
        params=[("record_ids", "a" * 64), ("record_ids", "b" * 64)],
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Saved backtest records failed integrity or protocol validation"
    }
    assert "record_json" not in response.text
