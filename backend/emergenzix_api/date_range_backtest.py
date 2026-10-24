"""Date-driven BTC historical strategy selection and one-week backtesting.

This first protocol deliberately supports verified Binance BTC/USDT spot 4-hour
candles and long-only SMA-crossover candidates. It never submits exchange orders.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .backtesting import run_sma_crossover
from .config import Settings
from .database import Database
from .dataset_store import persist_dataset
from .historical_data import (
    HistoricalArchiveUnavailable,
    fetch_binance_btc_4h_archive,
)
from .schemas import Bar, DatasetCreate


DATE_RANGE_BACKTEST_PROTOCOL_VERSION = "btc-date-range-backtest-v1"
BAR_INTERVAL = timedelta(hours=4)
BARS_PER_DAY = 6
SELECTION_TRAIN_FRACTION = 0.70
MINIMUM_VALIDATION_TRADES = 2

# Fixed, versioned candidate universe. Do not add/change candidates without
# incrementing DATE_RANGE_BACKTEST_PROTOCOL_VERSION.
SMA_CANDIDATES: tuple[tuple[int, int], ...] = (
    (3, 10),
    (5, 15),
    (5, 20),
    (7, 28),
    (10, 30),
    (15, 40),
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone offset")
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bars_for_requested_range(
    bars: Sequence[Bar], *, training_start: datetime, end_exclusive: datetime
) -> list[Bar]:
    """Keep exactly the training + scored period; the end timestamp is exclusive."""
    selected = [
        bar
        for bar in bars
        if training_start <= _utc(bar.timestamp) < end_exclusive
    ]
    return selected


def _compact_metrics(run: dict[str, Any], *, include_path: bool) -> dict[str, Any]:
    trades = run["trades"]
    wins = [float(trade["net_pnl"]) for trade in trades if trade["net_pnl"] > 0]
    losses = [float(trade["net_pnl"]) for trade in trades if trade["net_pnl"] < 0]
    total_fees = sum(
        float(trade["entry_fee"]) + float(trade["exit_fee"]) for trade in trades
    )
    result: dict[str, Any] = {
        "bars_count": int(run["bars_count"]),
        "initial_cash": float(run["initial_cash"]),
        "final_equity": float(run["final_equity"]),
        "net_pnl": round(float(run["final_equity"]) - float(run["initial_cash"]), 8),
        "total_return_pct": float(run["total_return_pct"]),
        "max_drawdown_pct": float(run["max_drawdown_pct"]),
        "number_of_trades": int(run["number_of_trades"]),
        "win_rate_pct": float(run["win_rate_pct"]),
        "benchmark_return_pct": float(run["benchmark_return_pct"]),
        "total_fees": round(total_fees, 8),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 6) if losses else None
        ),
        "average_trade_pnl": (
            round(
                sum(float(trade["net_pnl"]) for trade in trades) / len(trades),
                8,
            )
            if trades
            else 0.0
        ),
    }
    if include_path:
        result["equity_curve"] = run["equity_curve"]
        result["trades"] = trades
        result["assumptions"] = run["assumptions"]
    return result


def run_date_range_backtest(
    bars: Sequence[Bar],
    *,
    start_at: datetime,
    duration_days: int = 7,
    training_days: int = 90,
    initial_cash: float = 10_000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    """Select a fixed SMA candidate using only pre-test data, then score a holdout.

    The requested period is [start_at, start_at + duration_days), in UTC.
    Training and test bars must be a complete, gap-free 4-hour series.
    """
    start_utc = _utc(start_at)
    if any((start_utc.hour, start_utc.minute, start_utc.second, start_utc.microsecond)):
        raise ValueError("start_at must be aligned to 00:00 UTC for date-based runs")
    if not 1 <= duration_days <= 30:
        raise ValueError("duration_days must be between 1 and 30")
    if not 60 <= training_days <= 365:
        raise ValueError("training_days must be between 60 and 365")
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and greater than zero")
    if not math.isfinite(fee_bps) or not 0 <= fee_bps <= 1000:
        raise ValueError("fee_bps must be between 0 and 1000")
    if not math.isfinite(slippage_bps) or not 0 <= slippage_bps <= 1000:
        raise ValueError("slippage_bps must be between 0 and 1000")

    end_exclusive = start_utc + timedelta(days=duration_days)
    training_start = start_utc - timedelta(days=training_days)
    if end_exclusive > datetime.now(timezone.utc):
        raise ValueError("the requested test period must end in the past")

    selected_bars = _bars_for_requested_range(
        bars, training_start=training_start, end_exclusive=end_exclusive
    )
    expected_count = (training_days + duration_days) * BARS_PER_DAY
    expected_timestamps = [
        training_start + BAR_INTERVAL * index for index in range(expected_count)
    ]
    actual_timestamps = [_utc(bar.timestamp) for bar in selected_bars]
    if actual_timestamps != expected_timestamps:
        missing = sorted(set(expected_timestamps) - set(actual_timestamps))
        duplicates_or_unexpected = len(actual_timestamps) != len(set(actual_timestamps))
        if missing:
            first_missing = _utc_iso(missing[0])
            raise ValueError(
                "historical data is incomplete or has a 4-hour gap; "
                f"first missing candle: {first_missing}"
            )
        if duplicates_or_unexpected:
            raise ValueError("historical data contains duplicate candle timestamps")
        raise ValueError(
            "historical data does not exactly cover the requested training and test range"
        )

    training_count = training_days * BARS_PER_DAY
    test_count = duration_days * BARS_PER_DAY
    training_bars = selected_bars[:training_count]
    test_bars = selected_bars[training_count:]
    if len(test_bars) != test_count:
        raise ValueError("test data does not contain the expected number of candles")

    selection_index = int(len(training_bars) * SELECTION_TRAIN_FRACTION)
    if selection_index < max(slow for _, slow in SMA_CANDIDATES) + 2:
        raise ValueError("not enough pre-test history to warm up the candidate strategies")

    candidate_results: list[dict[str, Any]] = []
    for fast_window, slow_window in SMA_CANDIDATES:
        validation_run = run_sma_crossover(
            training_bars,
            fast_window=fast_window,
            slow_window=slow_window,
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            evaluation_start_index=selection_index,
        )
        metrics = _compact_metrics(validation_run, include_path=False)
        # A simple, explicit ranking rule: net return less a 0.5x maximum-drawdown
        # penalty. The test period is never passed to this candidate-ranking loop.
        score = (
            metrics["total_return_pct"]
            - 0.5 * metrics["max_drawdown_pct"]
        )
        candidate_results.append(
            {
                "candidate_id": f"sma-{fast_window}-{slow_window}",
                "strategy_type": "sma_crossover",
                "parameters": {
                    "fast_window": fast_window,
                    "slow_window": slow_window,
                },
                "eligible": metrics["number_of_trades"] >= MINIMUM_VALIDATION_TRADES,
                "selection_score": round(score, 6),
                **metrics,
            }
        )

    eligible_candidates = [item for item in candidate_results if item["eligible"]]
    ranking_pool = eligible_candidates or candidate_results
    ranked_candidates = sorted(
        ranking_pool,
        key=lambda item: (
            -float(item["selection_score"]),
            float(item["max_drawdown_pct"]),
            -int(item["number_of_trades"]),
            int(item["parameters"]["fast_window"]),
            int(item["parameters"]["slow_window"]),
        ),
    )
    selected_candidate = ranked_candidates[0]
    selection_status = (
        "selected_with_minimum_trade_evidence"
        if eligible_candidates
        else "insufficient_validation_trades"
    )

    selected_fast = int(selected_candidate["parameters"]["fast_window"])
    selected_slow = int(selected_candidate["parameters"]["slow_window"])
    # Only the chosen parameters are carried into the holdout run. The full series
    # provides indicator warm-up; execution and scored metrics begin at start_utc.
    test_run = run_sma_crossover(
        selected_bars,
        fast_window=selected_fast,
        slow_window=selected_slow,
        initial_cash=initial_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        evaluation_start_index=training_count,
    )
    test_metrics = _compact_metrics(test_run, include_path=True)

    return {
        "protocol_version": DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
        "market": {
            "symbol": "BTC/USDT",
            "exchange": "binance",
            "market_type": "spot",
            "timeframe": "4h",
        },
        "period": {
            "training_start_at": _utc_iso(training_start),
            "test_start_at": _utc_iso(start_utc),
            "test_end_at_exclusive": _utc_iso(end_exclusive),
            "last_test_candle_at": _utc_iso(_utc(test_bars[-1].timestamp)),
            "training_days": training_days,
            "duration_days": duration_days,
            "training_bars_count": training_count,
            "test_bars_count": test_count,
            "expected_test_bars_count": test_count,
            "test_data_complete": True,
        },
        "configuration": {
            "initial_cash": round(initial_cash, 8),
            "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps,
            "selection_train_fraction": SELECTION_TRAIN_FRACTION,
            "minimum_validation_trades": MINIMUM_VALIDATION_TRADES,
        },
        "strategy_selection": {
            "method": "fixed_sma_candidate_grid_on_pre_test_validation_slice",
            "selection_window_start_at": _utc_iso(
                _utc(training_bars[selection_index].timestamp)
            ),
            "selection_window_end_at": _utc_iso(
                _utc(training_bars[-1].timestamp)
            ),
            "selection_cutoff_exclusive": _utc_iso(start_utc),
            "score_definition": "net_return_pct - 0.5 * max_drawdown_pct",
            "status": selection_status,
            "warning": (
                "No candidate completed the minimum number of validation trades; "
                "the top-scoring candidate is provisional."
                if not eligible_candidates
                else None
            ),
            "future_visible_during_selection": False,
            "selected_candidate": {
                "candidate_id": selected_candidate["candidate_id"],
                "strategy_type": selected_candidate["strategy_type"],
                "parameters": selected_candidate["parameters"],
                "selection_score": selected_candidate["selection_score"],
            },
            "candidates": sorted(
                candidate_results,
                key=lambda item: (
                    -int(bool(item["eligible"])),
                    -float(item["selection_score"]),
                    float(item["max_drawdown_pct"]),
                    int(item["parameters"]["fast_window"]),
                    int(item["parameters"]["slow_window"]),
                ),
            ),
        },
        "test_result": test_metrics,
        "limitations": [
            "This protocol tests BTC/USDT spot candles only.",
            "The current execution engine is long-only and does not model futures shorts, funding, liquidation, leverage, or hedges.",
            "Historical simulation is not evidence of future profitability.",
            "Strategy selection uses only the pre-test period; the scored test period is not used to select parameters.",
        ],
        "safety": {
            "mode": "PAPER",
            "orders_created": False,
            "live_trading_enabled": False,
            "future_visible_during_selection": False,
            "test_period_used_for_selection": False,
        },
    }


def create_date_range_backtest(
    db: Database,
    *,
    start_date: date,
    duration_days: int = 7,
    training_days: int = 90,
    initial_cash: float = 10_000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> tuple[dict[str, Any], bool]:
    """Fetch verified public data, run the experiment, and persist an immutable record."""
    if not isinstance(start_date, date) or isinstance(start_date, datetime):
        raise ValueError("start_date must be a calendar date in YYYY-MM-DD format")
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_exclusive = start_at + timedelta(days=duration_days)
    training_start = start_at - timedelta(days=training_days)
    if end_exclusive > datetime.now(timezone.utc):
        raise ValueError("the requested test period must end in the past")

    raw_bars = fetch_binance_btc_4h_archive(
        start_at=training_start,
        end_at=end_exclusive,
    )
    bars = [Bar.model_validate(item) for item in raw_bars]
    report = run_date_range_backtest(
        bars,
        start_at=start_at,
        duration_days=duration_days,
        training_days=training_days,
        initial_cash=initial_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    dataset_bars = _bars_for_requested_range(
        bars, training_start=training_start, end_exclusive=end_exclusive
    )
    dataset_payload = DatasetCreate(
        source="binance_public_archive",
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="4h",
        bars=dataset_bars,
    )
    dataset_row, _ = persist_dataset(db, dataset_payload)
    dataset = {
        "id": dataset_row["id"],
        "sha256": dataset_row["content_sha256"],
        "source": dataset_row["source"],
        "exchange": dataset_row["exchange"],
        "symbol": dataset_row["symbol"],
        "timeframe": dataset_row["timeframe"],
        "start_at": dataset_row["start_at"],
        "end_at": dataset_row["end_at"],
        "bars_count": int(dataset_row["bars_count"]),
    }
    configuration = {
        "start_date": start_date.isoformat(),
        "duration_days": duration_days,
        "training_days": training_days,
        "initial_cash": round(initial_cash, 8),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
    }
    identity = {
        "dataset_sha256": dataset["sha256"],
        "protocol_version": DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
        "configuration": configuration,
    }
    record_id = _sha256(_canonical_json(identity))
    created_at = datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {
        "id": record_id,
        "record_type": "btc_date_range_backtest",
        "created_at": created_at,
        "protocol_version": DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
        "dataset": dataset,
        "configuration": configuration,
        "report": report,
        "safety": {
            "mode": "PAPER",
            "orders_created": False,
            "live_trading_enabled": False,
            "dataset_mutated": False,
        },
    }
    integrity_payload = {
        key: value for key, value in record.items() if key != "created_at"
    }
    record_sha256 = _sha256(_canonical_json(integrity_payload))
    record_json = _canonical_json(record)

    with db.connection() as connection:
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO date_range_backtest_records
                (id, dataset_id, dataset_sha256, start_at, end_at,
                 duration_days, training_days, protocol_version,
                 record_sha256, record_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                dataset["id"],
                dataset["sha256"],
                _utc_iso(start_at),
                _utc_iso(end_exclusive),
                duration_days,
                training_days,
                DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
                record_sha256,
                record_json,
                created_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM date_range_backtest_records WHERE id = ?",
            (record_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Date-range backtest record could not be persisted")
    if row["record_sha256"] != record_sha256:
        raise RuntimeError(
            "Stored result differs under the current protocol; increment the "
            "date-range backtest protocol version before changing result semantics"
        )
    stored_record = json.loads(row["record_json"])
    stored_record["record_sha256"] = row["record_sha256"]
    return stored_record, inserted.rowcount > 0


def write_report_artifacts(
    record: dict[str, Any], output_path: str | Path
) -> dict[str, str]:
    """Write a JSON report, human-readable Markdown, and CSV result tables."""
    json_path = Path(output_path).expanduser()
    if json_path.suffix.lower() != ".json":
        raise ValueError("output path must end in .json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    report = record["report"]
    selection = report["strategy_selection"]
    selected = selection["selected_candidate"]
    parameters = selected["parameters"]
    metrics = report["test_result"]
    stem = json_path.stem
    summary_path = json_path.with_name(f"{stem}.summary.csv")
    candidates_path = json_path.with_name(f"{stem}.candidates.csv")
    equity_path = json_path.with_name(f"{stem}.equity.csv")
    trades_path = json_path.with_name(f"{stem}.trades.csv")
    markdown_path = json_path.with_suffix(".md")

    def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    summary = {
        "record_id": record["id"],
        "record_sha256": record["record_sha256"],
        "symbol": report["market"]["symbol"],
        "timeframe": report["market"]["timeframe"],
        "test_start_at": report["period"]["test_start_at"],
        "test_end_at_exclusive": report["period"]["test_end_at_exclusive"],
        "test_bars_count": report["period"]["test_bars_count"],
        "selected_candidate": selected["candidate_id"],
        "fast_window": parameters["fast_window"],
        "slow_window": parameters["slow_window"],
        "initial_cash": metrics["initial_cash"],
        "final_equity": metrics["final_equity"],
        "net_pnl": metrics["net_pnl"],
        "total_return_pct": metrics["total_return_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "benchmark_return_pct": metrics["benchmark_return_pct"],
        "number_of_trades": metrics["number_of_trades"],
        "win_rate_pct": metrics["win_rate_pct"],
        "total_fees": metrics["total_fees"],
        "profit_factor": metrics["profit_factor"],
        "selection_status": selection["status"],
    }
    write_csv(summary_path, list(summary.keys()), [summary])

    candidate_rows = []
    for item in selection["candidates"]:
        candidate_rows.append(
            {
                "candidate_id": item["candidate_id"],
                "fast_window": item["parameters"]["fast_window"],
                "slow_window": item["parameters"]["slow_window"],
                "eligible": item["eligible"],
                "selection_score": item["selection_score"],
                "validation_return_pct": item["total_return_pct"],
                "validation_max_drawdown_pct": item["max_drawdown_pct"],
                "validation_trades": item["number_of_trades"],
                "validation_win_rate_pct": item["win_rate_pct"],
                "validation_benchmark_return_pct": item["benchmark_return_pct"],
            }
        )
    write_csv(
        candidates_path,
        [
            "candidate_id",
            "fast_window",
            "slow_window",
            "eligible",
            "selection_score",
            "validation_return_pct",
            "validation_max_drawdown_pct",
            "validation_trades",
            "validation_win_rate_pct",
            "validation_benchmark_return_pct",
        ],
        candidate_rows,
    )
    write_csv(equity_path, ["timestamp", "equity"], list(metrics.get("equity_curve", [])))
    write_csv(
        trades_path,
        [
            "entry_timestamp",
            "exit_timestamp",
            "entry_price",
            "exit_price",
            "quantity",
            "entry_fee",
            "exit_fee",
            "net_pnl",
        ],
        list(metrics.get("trades", [])),
    )

    def pct(value: Any) -> str:
        return "N/A" if value is None else f"{float(value):.4f}%"

    candidate_table = [
        "| Candidate | Fast | Slow | Validation return | Max drawdown | Trades | Score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in selection["candidates"]:
        candidate_table.append(
            "| {candidate} | {fast} | {slow} | {ret} | {drawdown} | {trades} | {score:.4f} |".format(
                candidate=item["candidate_id"],
                fast=item["parameters"]["fast_window"],
                slow=item["parameters"]["slow_window"],
                ret=pct(item["total_return_pct"]),
                drawdown=pct(item["max_drawdown_pct"]),
                trades=item["number_of_trades"],
                score=float(item["selection_score"]),
            )
        )
    trade_table = [
        "| Entry time | Exit time | Entry price | Exit price | Net PnL |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for trade in metrics["trades"]:
        trade_table.append(
            "| {entry} | {exit} | {entry_price:.4f} | {exit_price:.4f} | {pnl:.4f} |".format(
                entry=trade["entry_timestamp"],
                exit=trade["exit_timestamp"],
                entry_price=float(trade["entry_price"]),
                exit_price=float(trade["exit_price"]),
                pnl=float(trade["net_pnl"]),
            )
        )
    if len(trade_table) == 2:
        trade_table.append("| No completed trades | — | — | — | 0.0000 |")

    profit_factor_display = (
        "N/A"
        if metrics["profit_factor"] is None
        else f"{metrics['profit_factor']:.4f}"
    )
    markdown = "\n".join(
        [
            "# Emergenzix BTC Date-Range Backtest",
            "",
            f"- Record ID: {record['id']}",
            f"- Record SHA-256: {record['record_sha256']}",
            f"- Protocol: {record['protocol_version']}",
            f"- Test period (UTC): {report['period']['test_start_at']} to {report['period']['test_end_at_exclusive']} (end exclusive)",
            f"- Training period: {report['period']['training_days']} days; test candles: {report['period']['test_bars_count']}",
            f"- Selected candidate: {selected['candidate_id']} (SMA fast={parameters['fast_window']}, slow={parameters['slow_window']})",
            f"- Selection status: {selection['status']}",
            "",
            "## Test-week results",
            "",
            "| Metric | Result |",
            "| --- | ---: |",
            f"| Initial cash | {metrics['initial_cash']:.4f} |",
            f"| Final equity | {metrics['final_equity']:.4f} |",
            f"| Net PnL | {metrics['net_pnl']:.4f} |",
            f"| Net return | {pct(metrics['total_return_pct'])} |",
            f"| Buy-and-hold benchmark | {pct(metrics['benchmark_return_pct'])} |",
            f"| Maximum drawdown | {pct(metrics['max_drawdown_pct'])} |",
            f"| Closed trades | {metrics['number_of_trades']} |",
            f"| Win rate | {pct(metrics['win_rate_pct'])} |",
            f"| Total estimated fees | {metrics['total_fees']:.4f} |",
            f"| Profit factor | {profit_factor_display} |",
            "",
            "## Candidate selection (pre-test data only)",
            "",
            *candidate_table,
            "",
            "## Test-week closed trades",
            "",
            *trade_table,
            "",
            "## Interpretation and safety",
            "",
            "- Candidate selection is cut off before the test start; the test week does not select parameters.",
            "- Simulation includes configured fee and slippage assumptions; results are not a promise of future returns.",
            "- This first protocol is BTC/USDT spot, long-only SMA. It does not model futures shorts, funding, leverage, liquidation, or hedges.",
            "- No exchange orders were created; live trading remains disabled.",
            "",
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "summary_csv": str(summary_path),
        "candidates_csv": str(candidates_path),
        "equity_csv": str(equity_path),
        "trades_csv": str(trades_path),
    }


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a reproducible BTC one-week historical strategy experiment"
    )
    parser.add_argument("--start-date", required=True, type=_parse_date)
    parser.add_argument("--duration-days", type=int, default=7)
    parser.add_argument("--training-days", type=int, default=90)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        database = Database(
            args.db_path or settings.db_path,
            initial_paper_cash=settings.initial_paper_cash,
        )
        record, inserted = create_date_range_backtest(
            database,
            start_date=args.start_date,
            duration_days=args.duration_days,
            training_days=args.training_days,
            initial_cash=args.initial_cash,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
    except (ValueError, HistoricalArchiveUnavailable, RuntimeError) as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    output = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output is not None:
        try:
            artifacts = write_report_artifacts(record, args.output)
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "created" if inserted else "already_exists",
                    "record_id": record["id"],
                    "record_sha256": record["record_sha256"],
                    "artifacts": artifacts,
                    "database": args.db_path or settings.db_path,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
