"""Snapshot-verified, read-only weekly research batch reports.

Never rerun strategies, mutate a batch ledger, aggregate unrealized positions,
compound independent simulations or infer portfolio-level drawdown.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any

from .database import Database
from .date_range_comparison import DateRangeComparisonError, _comparison_row
from .research_batch_state import (
    BatchStateError, _instant, _sha, _validate_plan, _verify_saved_record,
)

BATCH_REPORT_VERSION = "btc-weekly-research-batch-report-v1"
_STATES = frozenset({"pending", "leased", "failed", "cancelled", "completed"})
_FAILURES = frozenset({
    "archive_unavailable", "data_incomplete", "worker_error", "lease_expired",
})


class BatchReportError(ValueError):
    """A sanitized error; never reveal raw SQLite payloads or credentials."""


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False,
    )


def _finite(value: object, *, lo: float | None = None,
            hi: float | None = None) -> float:
    if type(value) not in (int, float):
        raise BatchReportError("saved report contains invalid metrics")
    number = float(value)
    if not math.isfinite(number):
        raise BatchReportError("saved report contains invalid metrics")
    if lo is not None and number < lo or hi is not None and number > hi:
        raise BatchReportError("saved report contains invalid metrics")
    return number


def _summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "mean": round(fmean(values), 8),
        "median": round(median(values), 8),
        "minimum": round(min(values), 8),
        "maximum": round(max(values), 8),
    }


def _load_report_snapshot(
    conn: sqlite3.Connection, batch_id: str, *, now: datetime,
) -> dict[str, Any]:
    plan_row = conn.execute(
        "SELECT plan_json, created_at FROM research_batch_plans WHERE id = ?",
        (batch_id,),
    ).fetchone()
    if plan_row is None:
        raise BatchReportError("batch not found")
    try:
        plan = json.loads(plan_row["plan_json"])
        actual_id, _, periods = _validate_plan(plan, now=now)
        if actual_id != batch_id:
            raise BatchReportError("batch plan fingerprint does not match its ID")
    except (BatchStateError, ValueError, TypeError, KeyError) as exc:
        raise BatchReportError("batch plan failed integrity verification") from exc

    jobs = conn.execute(
        "SELECT id, batch_id, position, request_json, state, attempts, "
        "lease_expires_at, last_error_code, saved_record_id, saved_record_sha256 "
        "FROM research_batch_jobs WHERE batch_id = ? ORDER BY position",
        (batch_id,),
    ).fetchall()
    if len(jobs) != len(periods):
        raise BatchReportError("batch ledger has missing or extra jobs")

    state_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    recorded_ids: set[str] = set()
    for position, (job, expected) in enumerate(zip(jobs, periods), start=1):
        state = job["state"]
        if (
            job["batch_id"] != batch_id
            or job["position"] != position
            or job["id"] != expected["planned_job_id"]
            or state not in _STATES
            or type(job["attempts"]) is not int
            or not 0 <= job["attempts"] <= 3
        ):
            raise BatchReportError("batch job identity or state is inconsistent")
        try:
            request = json.loads(job["request_json"])
        except (TypeError, ValueError) as exc:
            raise BatchReportError("batch job request is invalid") from exc
        if _canonical(request) != _canonical(expected["backtest_request"]):
            raise BatchReportError("batch job differs from approved plan")
        code = job["last_error_code"]
        if code is not None and code not in _FAILURES:
            raise BatchReportError("batch job contains an unexpected failure code")
        if state == "leased" and not isinstance(job["lease_expires_at"], str):
            raise BatchReportError("active lease missing expiry")
        if state != "leased" and job["lease_expires_at"] is not None:
            raise BatchReportError("inactive job retains a lease")
        if state in ("pending", "cancelled") and job["attempts"] == 0 and code is not None:
            raise BatchReportError("unattempted job has an error code")
        if state in ("leased", "failed", "completed") and job["attempts"] == 0:
            raise BatchReportError("attempted job has zero attempts")
        if state == "completed":
            if code is not None:
                raise BatchReportError("completed job still has failure code")
            if (
                not _sha(job["saved_record_id"])
                or not _sha(job["saved_record_sha256"])
                or job["saved_record_id"] in recorded_ids
            ):
                raise BatchReportError("completed job missing unique saved result")
        elif job["saved_record_id"] is not None or job["saved_record_sha256"] is not None:
            raise BatchReportError("unfinished job claims a saved result")

        row_details: dict[str, Any] = {
            "sequence": position,
            "planned_job_id": job["id"],
            "test_start_at": expected["test_start_at"],
            "test_end_at_exclusive": expected["test_end_at_exclusive"],
            "state": state,
            "attempts": job["attempts"],
            "error_code": code,
            "lease_expires_at": job["lease_expires_at"] if state == "leased" else None,
            "lease_expired_at_snapshot": (
                job["lease_expires_at"] <= now.isoformat(timespec="microseconds").replace("+00:00", "Z")
                if state == "leased" else False
            ),
            "saved_record_id": None,
            "saved_record_sha256": None,
            "result": None,
        }
        if state == "completed":
            try:
                _verify_saved_record(
                    conn, job["saved_record_id"], job["saved_record_sha256"], request,
                )
                saved_row = conn.execute(
                    "SELECT * FROM date_range_backtest_records WHERE id = ?",
                    (job["saved_record_id"],),
                ).fetchone()
                observation = _comparison_row(saved_row)
            except (BatchStateError, DateRangeComparisonError, ValueError,
                    KeyError, TypeError, OverflowError) as exc:
                raise BatchReportError("completed result failed integrity verification") from exc
            if (
                observation["record_id"] != job["saved_record_id"]
                or observation["protocol_version"] != plan["backtest_protocol_version"]
                or observation["test_start_at"] != expected["test_start_at"]
                or observation["test_end_at_exclusive"] != expected["test_end_at_exclusive"]
                or observation["fee_bps_per_side"] != request["fee_bps"]
                or observation["slippage_bps_per_side"] != request["slippage_bps"]
            ):
                raise BatchReportError("completed result does not match planned experiment")
            _finite(observation["net_return_pct"], lo=-100)
            _finite(observation["max_drawdown_pct"], lo=0, hi=100)
            _finite(observation["benchmark_return_pct"], lo=-100)
            _finite(observation["win_rate_pct"], lo=0, hi=100)
            _finite(observation["total_fees"], lo=0)
            if (
                type(observation["number_of_trades"]) is not int
                or observation["number_of_trades"] < 0
                or not isinstance(observation["selected_candidate"], str)
                or not observation["selected_candidate"].strip()
            ):
                raise BatchReportError("completed result contains invalid strategy evidence")
            recorded_ids.add(job["saved_record_id"])
            completed.append(observation)
            row_details["saved_record_id"] = job["saved_record_id"]
            row_details["saved_record_sha256"] = job["saved_record_sha256"]
            row_details["result"] = observation
        state_counts[state] += 1
        if code is not None:
            failure_counts[code] += 1
        details.append(row_details)

    count = len(jobs)
    completed_count = state_counts["completed"]
    returns = [float(item["net_return_pct"]) for item in completed]
    drawdowns = [float(item["max_drawdown_pct"]) for item in completed]
    candidate_ids = {item["selected_candidate"] for item in completed}
    terminal = (
        state_counts["pending"] == 0 and state_counts["leased"] == 0
    )
    if terminal:
        status = "completed" if completed_count == count else "finished_with_incomplete_jobs"
    else:
        status = "in_progress"
    warnings = [
        "Counts and distributions are descriptive results of standalone historical simulations.",
        "Completed jobs are not independent statistical observations or a live portfolio.",
        "Do not compound saved weekly returns or infer portfolio drawdown or future profit.",
    ]
    if completed_count < count:
        warnings.append(
            "Incomplete/failed/cancelled jobs are excluded from financial distributions; "
            "they are not zero-return trading weeks."
        )
    if len(candidate_ids) > 1:
        warnings.append("Different test weeks selected different candidates; not a fixed-strategy record.")
    if any(job["lease_expired_at_snapshot"] for job in details):
        warnings.append("At least one leased job had expired at snapshot; lease recovery needs a claim.")
    report = {
        "report_version": BATCH_REPORT_VERSION,
        "batch_id": batch_id,
        "plan_sha256": plan["plan_sha256"],
        "backtest_protocol_version": plan["backtest_protocol_version"],
        "state": status,
        "total_planned": count,
        "state_counts": {
            key: state_counts[key]
            for key in ("pending", "leased", "completed", "failed", "cancelled")
        },
        "completion_rate_pct": round(100 * completed_count / count, 4),
        "failure_reason_counts": dict(sorted(failure_counts.items())),
        "completed_financial_observations": completed_count,
        "positive_completed_observations": sum(value > 0 for value in returns),
        "negative_completed_observations": sum(value < 0 for value in returns),
        "flat_completed_observations": sum(value == 0 for value in returns),
        "individual_net_return_distribution_pct": _summary(returns),
        "individual_max_drawdown_distribution_pct": _summary(drawdowns),
        "different_selected_candidates": len(candidate_ids) > 1,
        "jobs": details,
        "warnings": warnings,
        "financial_aggregation_type": "completed_independent_saved_observations_only",
        "compounded_portfolio_return_pct": None,
        "portfolio_max_drawdown_pct": None,
        "orders_created": False,
        "strategy_promotion_allowed": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
    }
    report["snapshot_sha256"] = hashlib.sha256(
        _canonical(report).encode("utf-8")
    ).hexdigest()
    return report


def build_research_batch_report(
    db: Database, batch_id: str, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Read a consistent SQLite snapshot, verifying every completed result."""
    if not _sha(batch_id):
        raise BatchReportError("invalid batch ID")
    moment = _instant(now)
    try:
        with db.connection() as conn:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            return _load_report_snapshot(conn, batch_id, now=moment)
    except sqlite3.Error as exc:
        raise BatchReportError("batch report database read failed") from exc


def render_research_batch_markdown(report: dict[str, Any]) -> str:
    """Human-readable report for already vetted service output only."""
    count = report["state_counts"]
    lines = [
        "# Emergenzix — Weekly Research Batch Report",
        "",
        "Batch: " + report["batch_id"],
        "Snapshot SHA-256: " + report["snapshot_sha256"],
        "State: " + report["state"],
        "Planned / completed: " + str(report["total_planned"]) + " / " + str(count["completed"]),
        "Completion rate: " + f'{report["completion_rate_pct"]:.2f}%',
        "Pending / leased / failed / cancelled: "
        + " / ".join(str(count[k]) for k in ("pending", "leased", "failed", "cancelled")),
        "Error codes: " + (
            ", ".join(k + "=" + str(v) for k, v in report["failure_reason_counts"].items())
            or "none"
        ),
        "",
        "## Completed observations only",
        "",
    ]
    if not report["completed_financial_observations"]:
        lines.append("No completed, verified backtest results; performance is unavailable.")
    else:
        lines.append(
            "Positive / negative / flat: "
            + " / ".join(
                str(report[key])
                for key in (
                    "positive_completed_observations",
                    "negative_completed_observations",
                    "flat_completed_observations",
                )
            )
        )
        for key, label in (
            ("individual_net_return_distribution_pct", "Per-record net return"),
            ("individual_max_drawdown_distribution_pct", "Per-record max drawdown"),
        ):
            dist = report[key]
            lines.append(
                label + " (mean / median / min / max): "
                + " / ".join(f'{dist[field]:+.4f}%'
                           for field in ("mean", "median", "minimum", "maximum"))
            )
    lines.extend([
        "",
        "## Planned test periods",
        "",
        "| # | UTC period | Job state | Attempts | Record ID | Candidate | Return | Drawdown |",
        "|---|---|---|---:|---|---|---:|---:|",
    ])
    for job in report["jobs"]:
        metrics = job["result"]
        lines.append(
            "| " + str(job["sequence"])
            + " | " + job["test_start_at"][:10] + " to " + job["test_end_at_exclusive"][:10]
            + " | " + job["state"]
            + " | " + str(job["attempts"])
            + " | " + (job["saved_record_id"] or "—")
            + " | " + (metrics["selected_candidate"] if metrics else "—")
            + " | " + (f'{metrics["net_return_pct"]:+.4f}%' if metrics else "—")
            + " | " + (f'{metrics["max_drawdown_pct"]:.4f}%' if metrics else "—")
            + " |"
        )
    lines.extend(["", "## Interpretation limits", ""])
    lines.extend("- " + warning for warning in report["warnings"])
    lines.append(
        "- No portfolio return is compounded and no PAPER/TESTNET/LIVE order permission is granted."
    )
    return "\n".join(lines) + "\n"


def write_batch_report(
    report: dict[str, Any], output: str | Path,
) -> dict[str, str]:
    """Explicit output path only; do not persist report automatically."""
    target = Path(output).expanduser()
    if target.suffix.lower() != ".json":
        raise BatchReportError("output filename must end in .json")
    target.parent.mkdir(parents=True, exist_ok=True)
    markdown = target.with_suffix(".md")
    if markdown == target:
        raise BatchReportError("invalid report destination")
    target.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n", encoding="utf-8",
    )
    markdown.write_text(render_research_batch_markdown(report), encoding="utf-8")
    return {"json": str(target), "markdown": str(markdown)}
