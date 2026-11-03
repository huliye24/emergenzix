#!/usr/bin/env python3
"""Build a deterministic 18-cell Turtle walk-forward matrix from lake Parquet."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from emergenzix_api.walkforward import (  # noqa: E402
    TIMEFRAME_INTERVALS,
    TURTLE_SYSTEMS,
    build_walk_forward_windows,
    resample_candles,
    run_turtle_walk_forward,
    validate_base_candles,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")
TIMEFRAMES = tuple(TIMEFRAME_INTERVALS)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("date must include a UTC offset")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_symbol(input_dir: Path, symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files = sorted(input_dir.glob(f"{symbol.lower()}-*.parquet"))
    if not files:
        raise ValueError(f"no Parquet files found for {symbol}")
    rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for path in files:
        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        if metadata.get(b"schema_version") != b"emergenzix-market-candle-v2":
            raise ValueError(f"unexpected schema in {path.name}")
        if metadata.get(b"timeframe") != b"15m":
            raise ValueError(f"unexpected base timeframe in {path.name}")
        rows.extend(table.to_pylist())
        manifest.append(
            {"name": path.name, "sha256": _sha256(path), "rows": table.num_rows}
        )
    return rows, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--start", type=_parse_utc, required=True)
    parser.add_argument("--end", type=_parse_utc, required=True)
    parser.add_argument("--train-days", type=int, default=120)
    parser.add_argument("--test-days", type=int, default=60)
    parser.add_argument("--step-days", type=int, default=60)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    windows = build_walk_forward_windows(
        start=args.start,
        end=args.end,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    cells: list[dict[str, Any]] = []
    file_manifest: dict[str, list[dict[str, Any]]] = {}
    for symbol in SYMBOLS:
        raw_rows, manifest = _read_symbol(args.input_dir, symbol)
        file_manifest[symbol] = manifest
        selected = validate_base_candles(raw_rows, start=args.start, end=args.end)
        for timeframe in TIMEFRAMES:
            bars = resample_candles(selected, timeframe=timeframe)
            for system, (entry_window, exit_window) in TURTLE_SYSTEMS.items():
                result = run_turtle_walk_forward(
                    bars,
                    windows=windows,
                    entry_window=entry_window,
                    exit_window=exit_window,
                    initial_cash=args.initial_cash,
                    fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                )
                cells.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "system": system,
                        "entry_window": entry_window,
                        "exit_window": exit_window,
                        **result,
                    }
                )

    report: dict[str, Any] = {
        "protocol": "emergenzix-turtle-walk-forward-v1",
        "mode": "BACKTEST_ONLY",
        "source": "immutable local mirror of market-data lake Parquet",
        "data_start": args.start.isoformat(),
        "data_end_exclusive": args.end.isoformat(),
        "train_days": args.train_days,
        "test_days": args.test_days,
        "step_days": args.step_days,
        "initial_cash_per_fold": args.initial_cash,
        "fee_bps_per_side": args.fee_bps,
        "slippage_bps_per_side": args.slippage_bps,
        "files": file_manifest,
        "cell_count": len(cells),
        "cells": cells,
    }
    if report["cell_count"] != 18:
        raise RuntimeError(f"expected 18 matrix cells, got {report['cell_count']}")
    report["report_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_bytes(_canonical(report) + b"\n")

    fieldnames = [
        "symbol",
        "timeframe",
        "system",
        "fold_count",
        "compounded_oos_return_pct",
        "worst_fold_drawdown_pct",
        "number_of_trades",
        "win_rate_pct",
        "compounded_benchmark_return_pct",
    ]
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cells)
    print(
        json.dumps(
            {
                "cell_count": len(cells),
                "folds_per_cell": len(windows),
                "report_sha256": report["report_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
