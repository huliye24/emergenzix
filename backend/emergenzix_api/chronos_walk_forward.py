"""Offline chronological Chronos benchmark for immutable crypto close series.

This module is intentionally not imported by the API.  It produces research
evidence only and exposes no exchange, credential, order, or promotion path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SYMBOLS = ("btcusdt", "ethusdt", "bnbusdt")
TIMEFRAMES = {"15m": (1, 672), "1h": (4, 168), "4h": (16, 42)}
MODEL_IDS = ("amazon/chronos-bolt-small", "amazon/chronos-2")


@dataclass(frozen=True)
class ForecastJob:
    symbol: str
    timeframe: str
    timestamp: str
    context: tuple[float, ...]
    last_close: float
    actual_close: float


def chronological_origins(
    length: int, *, context_length: int, count: int, spacing: int
) -> list[int]:
    """Return ascending target indices whose contexts use only earlier values."""
    if context_length < 50 or count < 1 or spacing < 1:
        raise ValueError("invalid walk-forward configuration")
    newest = length - 1
    origins = [newest - spacing * offset for offset in reversed(range(count))]
    if not origins or origins[0] < context_length:
        raise ValueError("series is too short for requested walk-forward origins")
    return origins


def summarize_predictions(
    actual_returns: Iterable[float],
    predicted_returns: Iterable[float],
    *,
    round_trip_cost_bps: float,
    include_forecast_error: bool = True,
) -> dict[str, float | int | None]:
    """Score sparse, independent one-step observations without compounding them."""
    actual = list(actual_returns)
    predicted = list(predicted_returns)
    if not actual or len(actual) != len(predicted):
        raise ValueError("prediction and actual samples must be non-empty and aligned")
    if round_trip_cost_bps < 0:
        raise ValueError("cost must not be negative")
    if any(not math.isfinite(value) for value in [*actual, *predicted]):
        raise ValueError("metrics require finite values")
    cost = round_trip_cost_bps / 10_000.0
    active = [forecast > 0 for forecast in predicted]
    gross = sum(outcome for outcome, is_active in zip(actual, active) if is_active)
    net = sum(
        outcome - cost for outcome, is_active in zip(actual, active) if is_active
    )
    return {
        "samples": len(actual),
        "mae_return_pct": (
            round(
                100.0
                * sum(
                    abs(forecast - outcome)
                    for forecast, outcome in zip(predicted, actual)
                )
                / len(actual),
                8,
            )
            if include_forecast_error
            else None
        ),
        "direction_accuracy_pct": round(
            100.0
            * sum((forecast > 0) == (outcome > 0) for forecast, outcome in zip(predicted, actual))
            / len(actual),
            8,
        ),
        "long_samples": sum(active),
        "sampled_gross_return_sum_pct": round(100.0 * gross, 8),
        "sampled_cost_adjusted_return_sum_pct": round(100.0 * net, 8),
    }


def _collection_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(root.glob("*.parquet"), key=lambda path: path.name)
    if not files:
        raise ValueError("no Parquet inputs found")
    hash_lines = []
    for path in files:
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        hash_lines.append(f"{file_hash}  {path.name}\n")
    for line in sorted(hash_lines):
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def _load_close_series(root: Path, symbol: str, timeframe: str):
    import pandas as pd
    import pyarrow.parquet as pq

    files = sorted(root.glob(f"{symbol}-*.parquet"), key=lambda path: path.name)
    if not files:
        raise ValueError(f"missing inputs for {symbol}")
    frames = [pq.read_table(path).to_pandas() for path in files]
    data = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    if data["timestamp"].duplicated().any():
        raise ValueError(f"duplicate timestamps for {symbol}")
    if not bool(data["is_closed"].all()) or bool(data["is_imputed"].any()):
        raise ValueError(f"unsafe candle status for {symbol}")
    deltas = data["timestamp"].diff().dropna()
    if bool((deltas != pd.Timedelta(minutes=15)).any()):
        raise ValueError(f"15m gaps for {symbol}")
    close = data.set_index("timestamp")["close"].astype(float)
    group_size, _ = TIMEFRAMES[timeframe]
    if group_size > 1:
        grouped = close.resample(timeframe).agg(["last", "count"])
        close = grouped.loc[grouped["count"] == group_size, "last"]
    if not close.index.is_monotonic_increasing or not close.index.is_unique:
        raise ValueError(f"invalid resampled index for {symbol} {timeframe}")
    return close


def _jobs(root: Path, *, context_length: int, origins: int) -> list[ForecastJob]:
    jobs: list[ForecastJob] = []
    for symbol in SYMBOLS:
        for timeframe, (_, spacing) in TIMEFRAMES.items():
            close = _load_close_series(root, symbol, timeframe)
            for target in chronological_origins(
                len(close),
                context_length=context_length,
                count=origins,
                spacing=spacing,
            ):
                context = tuple(float(value) for value in close.iloc[target - context_length : target])
                jobs.append(
                    ForecastJob(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=close.index[target].isoformat(),
                        context=context,
                        last_close=context[-1],
                        actual_close=float(close.iloc[target]),
                    )
                )
    return jobs


def _model_predictions(model_id: str, jobs: list[ForecastJob], cache: Path) -> list[float]:
    import torch
    from chronos import BaseChronosPipeline

    pipeline = BaseChronosPipeline.from_pretrained(
        model_id,
        device_map="cuda",
        dtype=torch.bfloat16,
        cache_dir=str(cache),
        local_files_only=True,
    )
    contexts = torch.tensor([job.context for job in jobs], dtype=torch.float32)
    if model_id.endswith("chronos-2"):
        raw = pipeline.predict(
            contexts.unsqueeze(1),
            prediction_length=1,
            batch_size=16,
            context_length=contexts.shape[1],
        )
        forecasts = [float(item[0, 10, 0].cpu()) for item in raw]
    else:
        raw = pipeline.predict(
            [row for row in contexts], prediction_length=1
        )
        forecasts = [float(value) for value in raw[:, 4, 0].cpu()]
    del pipeline, raw, contexts
    torch.cuda.empty_cache()
    return forecasts


def _baseline_prediction(job: ForecastJob, name: str) -> float:
    if name == "buy_and_hold":
        return 1.0
    if name == "sma_20_50":
        fast = sum(job.context[-20:]) / 20
        slow = sum(job.context[-50:]) / 50
        return 1.0 if fast > slow else -1.0
    raise ValueError(f"unsupported baseline: {name}")


def run_benchmark(
    root: Path,
    *,
    cache: Path,
    context_length: int = 512,
    origins: int = 8,
    round_trip_cost_bps: float = 30.0,
) -> dict[str, Any]:
    jobs = _jobs(root, context_length=context_length, origins=origins)
    actual = [(job.actual_close / job.last_close) - 1.0 for job in jobs]
    predictions: dict[str, list[float]] = {
        name: [_baseline_prediction(job, name) for job in jobs]
        for name in ("buy_and_hold", "sma_20_50")
    }
    for model_id in MODEL_IDS:
        forecast_close = _model_predictions(model_id, jobs, cache)
        predictions[model_id] = [
            (forecast / job.last_close) - 1.0
            for forecast, job in zip(forecast_close, jobs)
        ]

    metrics: dict[str, Any] = {}
    for name, forecast_returns in predictions.items():
        include_forecast_error = name in MODEL_IDS
        metrics[name] = {
            "overall": summarize_predictions(
                actual,
                forecast_returns,
                round_trip_cost_bps=round_trip_cost_bps,
                include_forecast_error=include_forecast_error,
            ),
            "by_symbol_timeframe": {},
        }
        for symbol in SYMBOLS:
            for timeframe in TIMEFRAMES:
                indexes = [
                    index
                    for index, job in enumerate(jobs)
                    if job.symbol == symbol and job.timeframe == timeframe
                ]
                metrics[name]["by_symbol_timeframe"][f"{symbol}:{timeframe}"] = (
                    summarize_predictions(
                        [actual[index] for index in indexes],
                        [forecast_returns[index] for index in indexes],
                        round_trip_cost_bps=round_trip_cost_bps,
                        include_forecast_error=include_forecast_error,
                    )
                )
    return {
        "protocol": "chronos-zero-shot-walk-forward-v1",
        "dataset_collection_sha256": _collection_sha256(root),
        "symbols": list(SYMBOLS),
        "timeframes": list(TIMEFRAMES),
        "context_length": context_length,
        "origins_per_symbol_timeframe": origins,
        "origin_spacing": "seven days",
        "round_trip_cost_bps_per_active_sample": round_trip_cost_bps,
        "metrics": metrics,
        "safety": {
            "mode": "RESEARCH_ONLY",
            "orders_created": False,
            "automatic_strategy_promotion": False,
            "paper_order_permission": False,
            "testnet_order_permission": False,
            "live_order_permission": False,
        },
        "limitations": [
            "Sparse one-step observations are independent samples, not a continuous portfolio backtest.",
            "Return sums are descriptive and are not compounded equity returns.",
            "Zero-shot forecasts were not tuned on the accepted dataset.",
            "Eight weekly origins per symbol and timeframe are not statistically sufficient.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-length", type=int, default=512)
    parser.add_argument("--origins", type=int, default=8)
    parser.add_argument("--round-trip-cost-bps", type=float, default=30.0)
    args = parser.parse_args()
    report = run_benchmark(
        args.data_root,
        cache=args.cache,
        context_length=args.context_length,
        origins=args.origins,
        round_trip_cost_bps=args.round_trip_cost_bps,
    )
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "dataset_collection_sha256": report["dataset_collection_sha256"],
        "safety": report["safety"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
