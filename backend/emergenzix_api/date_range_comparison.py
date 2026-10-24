"""Read-only comparison of persisted date-range backtest records."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence


COMPARISON_FIELDS = [
    "record_id",
    "protocol_version",
    "dataset_sha256",
    "test_start_at",
    "test_end_at_exclusive",
    "fee_bps_per_side",
    "slippage_bps_per_side",
    "selected_candidate",
    "strategy_type",
    "net_return_pct",
    "benchmark_return_pct",
    "max_drawdown_pct",
    "number_of_trades",
    "win_rate_pct",
    "total_fees",
]


class DateRangeComparisonError(ValueError):
    """Raised when saved records cannot be compared safely."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise DateRangeComparisonError(
            f"unsupported date-range record shape: missing {context}.{key}"
        )
    return mapping[key]


def _comparison_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        record = json.loads(row["record_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise DateRangeComparisonError("stored date-range record is not valid JSON") from exc
    if not isinstance(record, dict):
        raise DateRangeComparisonError("unsupported date-range record shape: root must be an object")

    integrity_payload = {
        key: value for key, value in record.items() if key != "created_at"
    }
    actual_hash = hashlib.sha256(
        _canonical_json(integrity_payload).encode("utf-8")
    ).hexdigest()
    if actual_hash != row["record_sha256"]:
        raise DateRangeComparisonError(
            f"record {row['id']} failed its integrity fingerprint check"
        )
    if record.get("record_type") != "btc_date_range_backtest":
        raise DateRangeComparisonError(
            f"record {row['id']} has an unsupported record type"
        )
    if record.get("protocol_version") != row["protocol_version"]:
        raise DateRangeComparisonError(
            f"record {row['id']} has inconsistent protocol metadata"
        )

    try:
        dataset = _require(record, "dataset", "record")
        configuration = _require(record, "configuration", "record")
        report = _require(record, "report", "record")
        period = _require(report, "period", "report")
        selection = _require(report, "strategy_selection", "report")
        selected = _require(selection, "selected_candidate", "strategy_selection")
        metrics = _require(report, "test_result", "report")
        result = {
            "record_id": _require(record, "id", "record"),
            "protocol_version": _require(record, "protocol_version", "record"),
            "dataset_sha256": _require(dataset, "sha256", "dataset"),
            "test_start_at": _require(period, "test_start_at", "period"),
            "test_end_at_exclusive": _require(
                period, "test_end_at_exclusive", "period"
            ),
            "fee_bps_per_side": _require(configuration, "fee_bps", "configuration"),
            "slippage_bps_per_side": _require(
                configuration, "slippage_bps", "configuration"
            ),
            "selected_candidate": _require(selected, "candidate_id", "selected_candidate"),
            "strategy_type": _require(selected, "strategy_type", "selected_candidate"),
            "net_return_pct": _require(metrics, "total_return_pct", "test_result"),
            "benchmark_return_pct": _require(
                metrics, "benchmark_return_pct", "test_result"
            ),
            "max_drawdown_pct": _require(metrics, "max_drawdown_pct", "test_result"),
            "number_of_trades": _require(metrics, "number_of_trades", "test_result"),
            "win_rate_pct": _require(metrics, "win_rate_pct", "test_result"),
            "total_fees": _require(metrics, "total_fees", "test_result"),
        }
    except TypeError as exc:
        raise DateRangeComparisonError(
            f"record {row['id']} has an unsupported nested record shape"
        ) from exc

    if result["record_id"] != row["id"]:
        raise DateRangeComparisonError(
            f"record {row['id']} has inconsistent record identity metadata"
        )
    dataset_sha = result["dataset_sha256"]
    if not isinstance(dataset_sha, str) or len(dataset_sha) != 64:
        raise DateRangeComparisonError(
            f"record {row['id']} has an invalid dataset fingerprint"
        )
    return result


def compare_saved_date_range_records(
    db_path: str | Path, record_ids: Sequence[str]
) -> dict[str, Any]:
    """Load and verify two or more records without mutating the database."""
    if len(record_ids) < 2:
        raise DateRangeComparisonError("at least two record IDs are required")
    if len(set(record_ids)) != len(record_ids):
        raise DateRangeComparisonError("record IDs must be unique")
    for record_id in record_ids:
        if len(record_id) != 64 or any(
            character not in "0123456789abcdef" for character in record_id
        ):
            raise DateRangeComparisonError(f"invalid record ID: {record_id}")

    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise DateRangeComparisonError(f"database does not exist: {resolved}")
    database_uri = f"file:{resolved.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(database_uri, uri=True, timeout=15.0)
        connection.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in record_ids)
        rows = connection.execute(
            f"SELECT * FROM date_range_backtest_records WHERE id IN ({placeholders})",
            tuple(record_ids),
        ).fetchall()
    except sqlite3.Error as exc:
        raise DateRangeComparisonError(
            "date-range records could not be read from the database"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()

    rows_by_id = {row["id"]: row for row in rows}
    missing = [record_id for record_id in record_ids if record_id not in rows_by_id]
    if missing:
        raise DateRangeComparisonError(f"record IDs not found: {', '.join(missing)}")
    observations = [_comparison_row(rows_by_id[record_id]) for record_id in record_ids]
    periods = {
        (item["test_start_at"], item["test_end_at_exclusive"])
        for item in observations
    }
    costs = {
        (item["fee_bps_per_side"], item["slippage_bps_per_side"])
        for item in observations
    }
    return {
        "comparison_type": "independent_saved_observations",
        "controlled_head_to_head": False,
        "different_test_periods": len(periods) > 1,
        "different_cost_assumptions": len(costs) > 1,
        "automatic_winner_selection": False,
        "observations": observations,
    }


def write_comparison_csv(comparison: dict[str, Any], output_path: str | Path) -> Path:
    """Write the verified observation table to CSV."""
    path = Path(output_path).expanduser()
    if path.suffix.lower() != ".csv":
        raise DateRangeComparisonError("comparison output path must end in .csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COMPARISON_FIELDS)
        writer.writeheader()
        writer.writerows(comparison["observations"])
    return path
