"""Date-driven BTC historical strategy selection and one-week backtesting.

This protocol supports verified Binance BTC/USDT spot 4-hour candles and a
small, versioned set of long-only candidates. It never submits exchange orders.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any, Sequence

from .backtesting import run_donchian_breakout, run_sma_crossover
from .database import Database
from .dataset_store import persist_dataset
from .historical_data import fetch_binance_btc_4h_archive
from .schemas import Bar, DatasetCreate


DATE_RANGE_BACKTEST_PROTOCOL_VERSION = "btc-date-range-backtest-v3"
BAR_INTERVAL = timedelta(hours=4)
BARS_PER_DAY = 6
SELECTION_TRAIN_FRACTION = 0.70
MINIMUM_VALIDATION_TRADES = 2
WALK_FORWARD_FOLDS = 3

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
DONCHIAN_CANDIDATES: tuple[tuple[int, int], ...] = ((20, 10),)


def _candidate_specs() -> list[dict[str, Any]]:
    """Return the complete, versioned candidate registry."""
    specs = [
        {
            "candidate_id": f"sma-{fast_window}-{slow_window}",
            "strategy_type": "sma_crossover",
            "parameters": {
                "fast_window": fast_window,
                "slow_window": slow_window,
            },
        }
        for fast_window, slow_window in SMA_CANDIDATES
    ]
    specs.extend(
        {
            "candidate_id": f"donchian-{entry_window}-{exit_window}",
            "strategy_type": "donchian_breakout",
            "parameters": {
                "entry_window": entry_window,
                "exit_window": exit_window,
            },
        }
        for entry_window, exit_window in DONCHIAN_CANDIDATES
    )
    return specs


def _run_candidate(
    candidate: dict[str, Any],
    bars: list[Bar],
    *,
    evaluation_start_index: int,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    """Run one registered candidate with shared execution assumptions."""
    parameters = candidate["parameters"]
    common = {
        "initial_cash": initial_cash,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "evaluation_start_index": evaluation_start_index,
    }
    if candidate["strategy_type"] == "sma_crossover":
        return run_sma_crossover(
            bars,
            fast_window=int(parameters["fast_window"]),
            slow_window=int(parameters["slow_window"]),
            **common,
        )
    if candidate["strategy_type"] == "donchian_breakout":
        return run_donchian_breakout(
            bars,
            entry_window=int(parameters["entry_window"]),
            exit_window=int(parameters["exit_window"]),
            **common,
        )
    raise ValueError(f"unsupported candidate type: {candidate['strategy_type']}")


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


def _rank_candidates(
    bars: list[Bar],
    *,
    evaluation_start_index: int,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Rank the fixed candidate grid without access to later candles."""
    candidate_results: list[dict[str, Any]] = []
    for candidate in _candidate_specs():
        validation_run = _run_candidate(
            candidate,
            bars,
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            evaluation_start_index=evaluation_start_index,
        )
        metrics = _compact_metrics(validation_run, include_path=False)
        score = metrics["total_return_pct"] - 0.5 * metrics["max_drawdown_pct"]
        candidate_results.append(
            {
                **candidate,
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
            str(item["candidate_id"]),
        ),
    )
    status = (
        "selected_with_minimum_trade_evidence"
        if eligible_candidates
        else "insufficient_validation_trades"
    )
    return candidate_results, ranked_candidates[0], status


def _walk_forward_validation(
    training_bars: list[Bar],
    *,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    """Evaluate three expanding-window folds over the final 30% of training data."""
    first_fold_index = int(len(training_bars) * SELECTION_TRAIN_FRACTION)
    validation_count = len(training_bars) - first_fold_index
    fold_size, remainder = divmod(validation_count, WALK_FORWARD_FOLDS)
    if fold_size < 1:
        raise ValueError("not enough pre-test history for walk-forward validation")

    folds: list[dict[str, Any]] = []
    fold_start = first_fold_index
    for fold_number in range(1, WALK_FORWARD_FOLDS + 1):
        fold_count = fold_size + (1 if fold_number <= remainder else 0)
        fold_end = fold_start + fold_count
        selection_bars = training_bars[:fold_start]
        selection_start = int(len(selection_bars) * SELECTION_TRAIN_FRACTION)
        _, selected, selection_status = _rank_candidates(
            selection_bars,
            evaluation_start_index=selection_start,
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        fold_run = _run_candidate(
            selected,
            training_bars[:fold_end],
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            evaluation_start_index=fold_start,
        )
        metrics = _compact_metrics(fold_run, include_path=False)
        enough_trades = metrics["number_of_trades"] >= MINIMUM_VALIDATION_TRADES
        folds.append(
            {
                "fold": fold_number,
                "selection_candles_count": fold_start,
                "selection_cutoff_exclusive": _utc_iso(
                    _utc(training_bars[fold_start].timestamp)
                ),
                "evaluation_start_at": _utc_iso(
                    _utc(training_bars[fold_start].timestamp)
                ),
                "evaluation_end_at_exclusive": _utc_iso(
                    _utc(training_bars[fold_end - 1].timestamp) + BAR_INTERVAL
                ),
                "evaluation_candles_count": fold_count,
                "selection_status": selection_status,
                "selected_candidate": {
                    "candidate_id": selected["candidate_id"],
                    "strategy_type": selected["strategy_type"],
                    "parameters": selected["parameters"],
                    "selection_score": selected["selection_score"],
                },
                "evidence_status": (
                    "sufficient_completed_trades"
                    if enough_trades
                    else "insufficient_completed_trades"
                ),
                "metrics": metrics,
            }
        )
        fold_start = fold_end

    selected_counts = Counter(
        fold["selected_candidate"]["candidate_id"] for fold in folds
    )
    return {
        "method": "three_fold_expanding_window",
        "folds_count": WALK_FORWARD_FOLDS,
        "evaluation_region_start_at": folds[0]["evaluation_start_at"],
        "evaluation_region_end_at_exclusive": folds[-1][
            "evaluation_end_at_exclusive"
        ],
        "folds": folds,
        "aggregate": {
            "median_return_pct": round(
                median(float(fold["metrics"]["total_return_pct"]) for fold in folds),
                6,
            ),
            "worst_max_drawdown_pct": round(
                max(float(fold["metrics"]["max_drawdown_pct"]) for fold in folds),
                6,
            ),
            "folds_with_completed_trades": sum(
                int(fold["metrics"]["number_of_trades"] > 0) for fold in folds
            ),
            "folds_with_minimum_trade_evidence": sum(
                int(fold["metrics"]["number_of_trades"] >= MINIMUM_VALIDATION_TRADES)
                for fold in folds
            ),
            "selected_candidate_counts": dict(sorted(selected_counts.items())),
            "same_candidate_selected_in_all_folds": len(selected_counts) == 1,
        },
        "future_visible_during_fold_selection": False,
        "automatic_promotion": False,
    }


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
    """Select a versioned candidate using only pre-test data, then score a holdout.

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

    candidate_results, selected_candidate, selection_status = _rank_candidates(
        training_bars,
        evaluation_start_index=selection_index,
        initial_cash=initial_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    walk_forward = _walk_forward_validation(
        training_bars,
        initial_cash=initial_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )

    # Only the chosen parameters are carried into the holdout run. The full series
    # provides indicator warm-up; execution and scored metrics begin at start_utc.
    test_run = _run_candidate(
        selected_candidate,
        selected_bars,
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
            "method": "versioned_candidate_registry_on_pre_test_validation_slice",
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
                if selection_status == "insufficient_validation_trades"
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
                    str(item["candidate_id"]),
                ),
            ),
        },
        "walk_forward_validation": walk_forward,
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
    report["data_provenance"] = {
        "dataset_id": dataset["id"],
        "dataset_sha256": dataset["sha256"],
        "source": dataset["source"],
        "archive_checksum_verified": True,
        "coverage_verified": bool(report["period"]["test_data_complete"]),
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
